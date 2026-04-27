#!/usr/bin/env bash
# Step Functions state machine 배포 (create-or-update, 멱등).
#
# 사용법:
#   IAM_ROLE_NAME=portfolio-mvp-step-functions-role scripts/deploy_step_functions.sh
#
# 환경변수:
#   IAM_ROLE_NAME        — 필수. Step Functions execution role 이름 (계정 내).
#                          이 role 은 Lambda invoke 권한을 가져야 함 (infra/README.md 참조).
#   AWS_REGION           — 기본 ap-northeast-2
#   STATE_MACHINE_NAME   — 기본 portfolio-mvp-screening
#   LAMBDA_NAME_PREFIX   — 기본 portfolio-mvp (deploy_lambda.sh 와 동일 규칙)
#                          state machine 정의의 <<LAMBDA_NAME>> 자리에
#                          ${LAMBDA_NAME_PREFIX}-run_screening 으로 치환됨.
#
# 사전 조건:
#   - run_screening Lambda 가 이미 배포되어 있어야 함 (deploy_lambda.sh)
#   - IAM role 이 infra/README.md 의 권한으로 생성되어 있어야 함
#
# 출력: 배포된 state machine 의 ARN.

set -euo pipefail

ROLE_NAME=${IAM_ROLE_NAME:?"IAM_ROLE_NAME 환경변수 필수 (Step Functions execution role 이름)"}
REGION="${AWS_REGION:-ap-northeast-2}"
NAME="${STATE_MACHINE_NAME:-portfolio-mvp-screening}"
PREFIX="${LAMBDA_NAME_PREFIX:-portfolio-mvp}"
LAMBDA_NAME="${PREFIX}-run_screening"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFINITION_TEMPLATE="$REPO_ROOT/infra/step_functions/screening_workflow.asl.json"

if [ ! -f "$DEFINITION_TEMPLATE" ]; then
  echo "[ERROR] 정의 파일이 없음: $DEFINITION_TEMPLATE" >&2
  exit 1
fi

# 계정 ID 조회 → role ARN 조립
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${ROLE_NAME}"

# 정의 템플릿에서 Lambda 이름 치환 (임시 파일에)
TMP_DEF=$(mktemp -t "screening-asl-XXXXXX.json")
trap 'rm -f "$TMP_DEF"' EXIT
sed "s|<<LAMBDA_NAME>>|${LAMBDA_NAME}|g" "$DEFINITION_TEMPLATE" > "$TMP_DEF"

echo "==> 설정"
echo "    Region          : $REGION"
echo "    State machine   : $NAME"
echo "    Lambda target   : $LAMBDA_NAME"
echo "    Execution role  : $ROLE_ARN"

# 기존 state machine ARN 조회
EXISTING_ARN=$(aws stepfunctions list-state-machines \
  --region "$REGION" \
  --query "stateMachines[?name=='${NAME}'].stateMachineArn | [0]" \
  --output text 2>/dev/null || true)

if [ -z "$EXISTING_ARN" ] || [ "$EXISTING_ARN" = "None" ]; then
  echo "==> Create state machine"
  RESULT=$(aws stepfunctions create-state-machine \
    --region "$REGION" \
    --name "$NAME" \
    --type STANDARD \
    --role-arn "$ROLE_ARN" \
    --definition "file://${TMP_DEF}" \
    --no-cli-pager \
    --output json)
  ARN=$(echo "$RESULT" | python -c "import json,sys;print(json.load(sys.stdin)['stateMachineArn'])")
else
  echo "==> Update state machine ($EXISTING_ARN)"
  aws stepfunctions update-state-machine \
    --region "$REGION" \
    --state-machine-arn "$EXISTING_ARN" \
    --role-arn "$ROLE_ARN" \
    --definition "file://${TMP_DEF}" \
    --no-cli-pager \
    --output json > /dev/null
  ARN="$EXISTING_ARN"
fi

echo "==> 완료"
echo "    State machine ARN: $ARN"
echo
echo "수동 실행 테스트 (예):"
echo "  aws stepfunctions start-execution \\"
echo "    --state-machine-arn '$ARN' \\"
echo "    --input '{\"as_of_date\": \"$(date -u +%Y-%m-%d)\"}'"
