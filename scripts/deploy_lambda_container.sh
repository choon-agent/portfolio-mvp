#!/usr/bin/env bash
# 컨테이너 이미지 Lambda 빌드·푸시·배포 — run_optimizer (04 §7.2, 컨테이너 방침 첫 적용)
#
# 사용법:
#   scripts/deploy_lambda_container.sh [<aws_function_name>]
#   (기본: portfolio-mvp-run_optimizer)
#
# 흐름:
#   1. ECR 리포 없으면 생성 (portfolio-mvp/run_optimizer)
#   2. docker build (linux/amd64) — git SHA 태그
#   3. 빌드 스모크: 컨테이너 안에서 핵심 import 검증 (pyarrow 사고 교훈 —
#      배포 전에 import 깨짐을 잡는다)
#   4. ECR push
#   5. Lambda 함수 없으면 create (Image 패키지, run_screening role 재사용,
#      timeout 300 / memory 1024 — 04 §7.2), 있으면 update-function-code
#
# 전제: Docker daemon 실행 중, AWS CLI 자격 증명.

set -euo pipefail

FUNCTION_NAME=${1:-portfolio-mvp-run_optimizer}
REGION=${AWS_REGION:-ap-northeast-2}
REPO="portfolio-mvp/run_optimizer"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="$(git -C "$ROOT" rev-parse --short HEAD)"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
ECR="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
IMAGE_URI="$ECR/$REPO:$TAG"

echo "==> ECR 리포 확인/생성: $REPO"
aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO" --region "$REGION" \
       --image-scanning-configuration scanOnPush=true --no-cli-pager >/dev/null

echo "==> docker build (linux/amd64) — tag $TAG"
# --provenance/--sbom=false: buildx 의 attestation manifest list 는 Lambda 가
# 미지원 ("image manifest ... media type not supported") — 단일 매니페스트 강제.
# buildx 플러그인 필요 (레거시 빌더는 cross-platform export 버그).
docker build --platform linux/amd64 --provenance=false --sbom=false \
  -f "$ROOT/infra/docker/optimizer.Dockerfile" -t "$REPO:$TAG" "$ROOT"

echo "==> 빌드 스모크 (컨테이너 내 import 검증)"
docker run --rm --platform linux/amd64 --entrypoint python "$REPO:$TAG" \
  -c "import pypfopt, pyarrow, pandas, numpy; import lambdas.run_optimizer.handler; print('smoke OK')"

echo "==> ECR push: $IMAGE_URI"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ECR" >/dev/null
docker tag "$REPO:$TAG" "$IMAGE_URI"
docker push "$IMAGE_URI"

if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "==> 기존 함수 코드 갱신: $FUNCTION_NAME"
  aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION" || true
  aws lambda update-function-code --function-name "$FUNCTION_NAME" \
    --image-uri "$IMAGE_URI" --publish --region "$REGION" --no-cli-pager \
    --query "[FunctionName,Version]" --output text
else
  echo "==> 함수 신규 생성: $FUNCTION_NAME (run_screening role 재사용)"
  ROLE="$(aws lambda get-function-configuration --function-name portfolio-mvp-run_screening \
          --region "$REGION" --query Role --output text)"
  BUCKET="$(aws lambda get-function-configuration --function-name portfolio-mvp-run_screening \
          --region "$REGION" --query 'Environment.Variables.S3_BUCKET' --output text)"
  aws lambda create-function --function-name "$FUNCTION_NAME" \
    --package-type Image --code ImageUri="$IMAGE_URI" \
    --role "$ROLE" --timeout 300 --memory-size 1024 \
    --environment "Variables={S3_BUCKET=$BUCKET,LOG_LEVEL=INFO}" \
    --architectures x86_64 --region "$REGION" --no-cli-pager \
    --query "[FunctionName,State]" --output text
fi

aws lambda wait function-active-v2 --function-name "$FUNCTION_NAME" --region "$REGION"
echo "==> 완료: $FUNCTION_NAME ($IMAGE_URI)"
