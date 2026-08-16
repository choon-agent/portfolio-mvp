# run_optimizer — 4단계 최적화 Lambda (컨테이너 이미지, 04 §7.2)
# 컨테이너 방침 첫 적용 (infra/README "50MB 재근접 시 컨테이너 전환 1순위").
# 빌드는 리포 루트에서: docker build -f infra/docker/optimizer.Dockerfile .
FROM public.ecr.aws/lambda/python:3.12

# 의존성 먼저 (레이어 캐시 — src 변경 시 재설치 회피)
COPY infra/docker/optimizer-requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# zip 레이아웃과 동일한 모듈 구조 (/var/task 루트)
# screening 은 agents.bull_bear.schemas → screening.schemas(PeerComparable) 의존
COPY src/common      ${LAMBDA_TASK_ROOT}/common
COPY src/screening   ${LAMBDA_TASK_ROOT}/screening
COPY src/agents      ${LAMBDA_TASK_ROOT}/agents
COPY src/optimizer   ${LAMBDA_TASK_ROOT}/optimizer
COPY src/lambdas/run_optimizer ${LAMBDA_TASK_ROOT}/lambdas/run_optimizer
RUN touch ${LAMBDA_TASK_ROOT}/lambdas/__init__.py

CMD ["lambdas.run_optimizer.handler.lambda_handler"]
