# run_rebalancer — 5단계 리밸런싱 Lambda (전용 컨테이너 이미지, 05 §7.2 확정 ⑤)
# pyarrow(OHLCV parquet) 필요 → zip 슬리밍 사고 전례(302f3cb)로 컨테이너.
# optimizer 이미지와 독립 배포 (커플링 회피) — cvxpy/PyPortfolioOpt 불필요로 경량.
# 빌드는 리포 루트에서: docker build -f infra/docker/rebalancer.Dockerfile .
FROM public.ecr.aws/lambda/python:3.12

# 의존성 먼저 (레이어 캐시 — src 변경 시 재설치 회피)
COPY infra/docker/rebalancer-requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# zip 레이아웃과 동일한 모듈 구조 (/var/task 루트)
# optimizer 는 rebalancer.trade_rules → optimizer.schemas(비중 상수) 의존만
COPY src/common      ${LAMBDA_TASK_ROOT}/common
COPY src/optimizer   ${LAMBDA_TASK_ROOT}/optimizer
COPY src/rebalancer  ${LAMBDA_TASK_ROOT}/rebalancer
COPY src/lambdas/run_rebalancer ${LAMBDA_TASK_ROOT}/lambdas/run_rebalancer
RUN touch ${LAMBDA_TASK_ROOT}/lambdas/__init__.py

CMD ["lambdas.run_rebalancer.handler.lambda_handler"]
