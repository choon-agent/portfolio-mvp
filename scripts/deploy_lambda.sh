#!/usr/bin/env bash
# 단일 Lambda 패키징 및 배포.
#
# 사용법:
#   scripts/deploy_lambda.sh <lambda_dir_name> <aws_function_name>
#
# 예:
#   scripts/deploy_lambda.sh update_constituents portfolio-mvp-update_constituents
#
# 전제:
#   - AWS Lambda 함수가 이미 생성되어 있음 (IaC 또는 콘솔)
#   - 런타임 python3.12, 핸들러 문자열 "lambdas.<dir>.handler.lambda_handler"
#   - zip 루트 레이아웃은 src/ 디렉토리와 동일:
#       lambdas/<dir>/handler.py   + common/*  + screening/*  + 의존성
#     (handler.py 의 sys.path 조작이 /var/task 를 기준으로 common/screening 을 import)
#
# 의존성:
#   - src/lambdas/<dir>/requirements.txt 가 있으면 우선 사용
#   - 없으면 루트 requirements.txt 사용
#   - 둘 다 없으면 의존성 설치 건너뜀 (Lambda 런타임 제공 boto3 만 사용하는 경우)

set -euo pipefail

LAMBDA_DIR=${1:?"첫 번째 인자: src/lambdas/ 하위 디렉토리 이름"}
FUNCTION_NAME=${2:?"두 번째 인자: AWS Lambda 함수 이름"}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="$REPO_ROOT/src"
LAMBDA_SRC="$SRC_DIR/lambdas/$LAMBDA_DIR"
BUILD_DIR="$(mktemp -d -t "lambda-${LAMBDA_DIR}-XXXXXX")"
OUT_DIR="$REPO_ROOT/build"
ZIP_PATH="$OUT_DIR/${LAMBDA_DIR}.zip"

cleanup() { rm -rf "$BUILD_DIR"; }
trap cleanup EXIT

if [ ! -d "$LAMBDA_SRC" ]; then
  echo "[ERROR] Lambda 소스 디렉토리가 없음: $LAMBDA_SRC" >&2
  exit 1
fi

# 컨테이너 이미지 함수 가드 — zip 업데이트 불가 ("Please provide ImageUri").
# 패키징 전에 확인해 시간 낭비·오배포 방지 (2026-08-17 CI 실패 교훈).
PKG_TYPE=$(aws lambda get-function-configuration --function-name "$FUNCTION_NAME" \
  --query PackageType --output text 2>/dev/null || echo "Zip")
if [ "$PKG_TYPE" = "Image" ]; then
  echo "[ERROR] $FUNCTION_NAME 은 컨테이너 이미지 함수 — 이 스크립트(zip)가 아니라" >&2
  echo "        scripts/deploy_lambda_container.sh 를 사용할 것" >&2
  exit 1
fi
if [ ! -f "$LAMBDA_SRC/handler.py" ]; then
  echo "[ERROR] handler.py 없음: $LAMBDA_SRC/handler.py" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
rm -f "$ZIP_PATH"

echo "==> 소스 복사"
mkdir -p "$BUILD_DIR/lambdas"
cp -R "$LAMBDA_SRC" "$BUILD_DIR/lambdas/"
[ -d "$SRC_DIR/common" ]    && cp -R "$SRC_DIR/common"    "$BUILD_DIR/common"
[ -d "$SRC_DIR/screening" ] && cp -R "$SRC_DIR/screening" "$BUILD_DIR/screening"
# agents/ 는 Bull/Bear Lambda (M2 #7) 전용. 다른 Lambda 는 import 안 하지만 zip
# 에 포함되어도 무해 — agents/prompts/*.md 는 agent.py 가 런타임 로드.
[ -d "$SRC_DIR/agents" ]    && cp -R "$SRC_DIR/agents"    "$BUILD_DIR/agents"

# Python 3.12 에서는 namespace package 가 동작하지만, 명시적 __init__.py 로 안전하게
touch "$BUILD_DIR/lambdas/__init__.py"
touch "$BUILD_DIR/lambdas/$LAMBDA_DIR/__init__.py"
if [ -d "$BUILD_DIR/agents" ]; then
  touch "$BUILD_DIR/agents/__init__.py"
  [ -d "$BUILD_DIR/agents/bull_bear" ] && touch "$BUILD_DIR/agents/bull_bear/__init__.py"
fi

# 번들에 포함되면 안 되는 것들 제거
find "$BUILD_DIR" -type d \( -name __pycache__ -o -name tests -o -name .pytest_cache \) -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -type f \( -name '*.pyc' -o -name 'requirements.txt' \) -delete 2>/dev/null || true

echo "==> 의존성 결정"
REQ_FILE=""
if [ -f "$LAMBDA_SRC/requirements.txt" ]; then
  REQ_FILE="$LAMBDA_SRC/requirements.txt"
elif [ -f "$REPO_ROOT/requirements.txt" ]; then
  REQ_FILE="$REPO_ROOT/requirements.txt"
fi

if [ -n "$REQ_FILE" ]; then
  echo "    사용 파일: $REQ_FILE"
  # Lambda 런타임(amazonlinux 2023, x86_64) 호환 wheel 만 설치
  python -m pip install \
    --quiet \
    --target "$BUILD_DIR" \
    --platform manylinux2014_x86_64 \
    --implementation cp \
    --python-version 3.12 \
    --only-binary=:all: \
    --upgrade \
    -r "$REQ_FILE"
else
  echo "    requirements.txt 없음 — 의존성 설치 건너뜀"
fi

# 설치 의존성 슬림화 — Lambda 직접 업로드 50MB(zip) 한계 회피.
# pyarrow(~132MB)가 번들의 85%. 코드는 pyarrow core + compute + parquet 만 사용.
#
# ⚠ 삭제 가능 여부는 Python lazy import 가 아니라 ELF DT_NEEDED 기준으로 판단할 것.
#   pyarrow 19.x 휠에서 lib.cpython-*.so(= import pyarrow 시 즉시 로드)와
#   libarrow_python.so 는 libarrow_substrait.so.1900 를 DT_NEEDED 로 갖는다 —
#   삭제 시 모든 pyarrow import 가 Runtime.ImportModuleError 로 즉사 (2026-06-29,
#   07-06 스크리닝 장애 원인). C++ substrait lib(5.2MB, zip ~1.5MB)은 유지하고
#   Cython 모듈 _substrait.so(pyarrow.substrait lazy import 전용)만 제거.
#   libarrow_flight 는 eager 체인 DT_NEEDED 에 없어 제거 안전 (_flight.so 와
#   이에 종속된 libarrow_python_flight 도 함께 제거). gandiva 는 19.x 휠에 없음.
#   C++ 헤더(include/)는 런타임 불요.
if [ -d "$BUILD_DIR/pyarrow" ]; then
  echo "==> pyarrow 슬림화 (Flight/include 제거 — libarrow_substrait 는 core 의존성이라 유지)"
  rm -f "$BUILD_DIR"/pyarrow/libarrow_flight*.so* \
        "$BUILD_DIR"/pyarrow/libarrow_python_flight*.so* \
        "$BUILD_DIR"/pyarrow/libgandiva*.so* \
        "$BUILD_DIR"/pyarrow/_flight*.so \
        "$BUILD_DIR"/pyarrow/_substrait*.so \
        "$BUILD_DIR"/pyarrow/_gandiva*.so 2>/dev/null || true
  rm -rf "$BUILD_DIR/pyarrow/include" 2>/dev/null || true
  # 가드: core 가 DT_NEEDED 로 요구하는 .so 가 번들에 있는지 확인 (재발 방지, 버전 무관)
  for lib in libarrow libarrow_python libarrow_substrait \
             libarrow_dataset libarrow_acero libparquet; do
    if ! ls "$BUILD_DIR/pyarrow/$lib".so* >/dev/null 2>&1; then
      echo "[ERROR] pyarrow 슬림화 후 core 필수 라이브러리 누락: $lib.so*" >&2
      exit 1
    fi
  done
fi
# 설치 패키지의 테스트·Cython 소스·타입 스텁 제거 (런타임 불요)
find "$BUILD_DIR" -type d \( -name tests -o -name __pycache__ \) -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -type f \( -name '*.pyx' -o -name '*.pxd' -o -name '*.pyi' \) -delete 2>/dev/null || true

echo "==> Zip 생성"
( cd "$BUILD_DIR" && zip -qr "$ZIP_PATH" . -x '*.pyc' '*__pycache__*' )
SIZE=$(du -h "$ZIP_PATH" | cut -f1)
echo "    $ZIP_PATH ($SIZE)"

echo "==> 배포: $FUNCTION_NAME"
# 이전 업데이트가 아직 진행 중이면 대기
aws lambda wait function-updated --function-name "$FUNCTION_NAME" || true

aws lambda update-function-code \
  --function-name "$FUNCTION_NAME" \
  --zip-file "fileb://$ZIP_PATH" \
  --publish \
  --no-cli-pager \
  --output json > "$BUILD_DIR/deploy.json"

VERSION=$(python -c "import json,sys;print(json.load(open('$BUILD_DIR/deploy.json')).get('Version','?'))")
REVISION=$(python -c "import json,sys;print(json.load(open('$BUILD_DIR/deploy.json')).get('RevisionId','?'))")
echo "==> 완료: $FUNCTION_NAME  version=$VERSION  revision=$REVISION"
