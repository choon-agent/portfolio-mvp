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
#   LAMBDA_NAME_PREFIX   — 기본 portfolio-mvp (deploy_lambda.sh 와 동일 규칙).
#                          정의 파일의 placeholder 4개 치환:
#                            <<RUN_SCREENING_LAMBDA>> → ${PREFIX}-run_screening
#                            <<BULL_LAMBDA>>          → ${PREFIX}-agent_bullbear_bull
#                            <<BEAR_LAMBDA>>          → ${PREFIX}-agent_bullbear_bear
#                            <<SCENARIO_LAMBDA>>      → ${PREFIX}-agent_scenario
#                            <<OPTIMIZER_LAMBDA>>     → ${PREFIX}-run_optimizer
#
# 사전 조건:
#   - run_screening, agent_bullbear_bull, agent_bullbear_bear, agent_scenario
#     Lambda 모두 배포됨 (deploy_lambda.sh + GitHub Actions)
#   - IAM role 이 infra/README.md 의 권한으로 생성되어 있어야 함
#     (위 4개 Lambda invoke 권한 포함)
#
# 출력: 배포된 state machine 의 ARN.

set -euo pipefail

ROLE_NAME=${IAM_ROLE_NAME:?"IAM_ROLE_NAME 환경변수 필수 (Step Functions execution role 이름)"}
REGION="${AWS_REGION:-ap-northeast-2}"
NAME="${STATE_MACHINE_NAME:-portfolio-mvp-screening}"
PREFIX="${LAMBDA_NAME_PREFIX:-portfolio-mvp}"
RUN_SCREENING_LAMBDA="${PREFIX}-run_screening"
BULL_LAMBDA="${PREFIX}-agent_bullbear_bull"
BEAR_LAMBDA="${PREFIX}-agent_bullbear_bear"
SCENARIO_LAMBDA="${PREFIX}-agent_scenario"
OPTIMIZER_LAMBDA="${PREFIX}-run_optimizer"

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
sed -e "s|<<RUN_SCREENING_LAMBDA>>|${RUN_SCREENING_LAMBDA}|g" \
    -e "s|<<BULL_LAMBDA>>|${BULL_LAMBDA}|g" \
    -e "s|<<BEAR_LAMBDA>>|${BEAR_LAMBDA}|g" \
    -e "s|<<SCENARIO_LAMBDA>>|${SCENARIO_LAMBDA}|g" \
    -e "s|<<OPTIMIZER_LAMBDA>>|${OPTIMIZER_LAMBDA}|g" \
    "$DEFINITION_TEMPLATE" > "$TMP_DEF"

# 치환 누락 가드 — << 가 남아 있으면 정의 오류
if grep -q '<<' "$TMP_DEF"; then
  echo "[ERROR] 치환 안 된 placeholder 가 남아 있음:" >&2
  grep '<<' "$TMP_DEF" >&2
  exit 1
fi

echo "==> 설정"
echo "    Region              : $REGION"
echo "    State machine       : $NAME"
echo "    RunScreening Lambda : $RUN_SCREENING_LAMBDA"
echo "    Bull Lambda         : $BULL_LAMBDA"
echo "    Bear Lambda         : $BEAR_LAMBDA"
echo "    Scenario Lambda     : $SCENARIO_LAMBDA"
echo "    Optimizer Lambda    : $OPTIMIZER_LAMBDA"
echo "    Execution role      : $ROLE_ARN"

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
