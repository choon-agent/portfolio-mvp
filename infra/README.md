# 인프라 (M1)

> 스크리닝 워크플로우용 AWS 리소스 정의와 배포 가이드.
> 설계: [`docs/01-screening.md` §4](../docs/01-screening.md)

## 디렉토리 구조

```
infra/
├── README.md                                  ← 이 파일
└── step_functions/
    └── screening_workflow.asl.json            ← Step Functions 상태 정의
```

EventBridge 스케줄과 IAM 역할은 별도 파일을 두지 않고 **이 README 의 명령어로 직접 관리**.
M2 이후 워크플로우가 복잡해지면 SAM 또는 CDK 로 이전 검토 (CHARTER `IaC: TBD`).

## 리소스 개요

| 리소스 | 이름 (기본) | 용도 |
|---|---|---|
| Lambda | `portfolio-mvp-run_screening` | 스크리닝 실행 — `pipeline.run_screening` |
| Step Functions | `portfolio-mvp-screening` | run_screening 호출 + 재시도. M2 에 Bull/Bear Map 추가 예정 |
| EventBridge Rule | `portfolio-mvp-weekly-screening` | Mon 06:00 ET (11:00 UTC) cron 트리거 |
| IAM Role (Lambda) | `portfolio-mvp-run_screening-role` | Lambda 실행 (S3, Secrets) |
| IAM Role (Step Functions) | `portfolio-mvp-step-functions-role` | Lambda invoke |
| IAM Role (EventBridge) | `portfolio-mvp-eventbridge-role` | Step Functions startExecution |

## 배포 순서

### 초기 1회 수동
1. **IAM 역할 4개 생성** — 아래 §IAM 역할 (3개 런타임 + CI 1개 추가 권한) 참고
2. **Lambda 환경변수 설정** — Lambda 콘솔에서 `S3_BUCKET`, `FMP_SECRET_ID` 입력
3. **EventBridge 스케줄 생성** — 아래 §EventBridge 스케줄 명령어 그대로

### 코드/정의 변경 시 (자동)
- `src/lambdas/**`, `src/common/**`, `src/screening/**` 변경 → [`.github/workflows/deploy-lambdas.yml`](../.github/workflows/deploy-lambdas.yml) 가 Lambda 자동 배포
- `infra/step_functions/**` 변경 → [`.github/workflows/deploy-step-functions.yml`](../.github/workflows/deploy-step-functions.yml) 가 state machine 자동 배포
- 두 워크플로우 모두 멱등 (create-or-update). 트리거되지 않으면 변경 없음.

### 로컬 수동 실행 (옵션)
GitHub Actions 우회하고 즉시 배포가 필요할 때:
```bash
# Lambda
scripts/deploy_lambda.sh run_screening portfolio-mvp-run_screening

# Step Functions
IAM_ROLE_NAME=portfolio-mvp-step-functions-role scripts/deploy_step_functions.sh
```
로컬엔 `aws configure` 필요. CI 와 다른 자격증명을 쓰지 않도록 주의.

## IAM 역할

### 1) Lambda 실행 역할 (`portfolio-mvp-run_screening-role`)

이미 다른 Lambda 들이 사용하는 패턴. **신뢰 정책**:
```json
{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}
```

**권한** (인라인 또는 관리형 + 인라인 조합):
- `AWSLambdaBasicExecutionRole` (관리형) — CloudWatch Logs
- 인라인 정책 (S3 + Secrets Manager):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:HeadObject"],
      "Resource": "arn:aws:s3:::<S3_BUCKET>/*"
    },
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:ap-northeast-2:<ACCOUNT>:secret:<FMP_SECRET_ID>-*"
    }
  ]
}
```

### 2) Step Functions 실행 역할 (`portfolio-mvp-step-functions-role`)

**신뢰 정책**:
```json
{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "states.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}
```

**권한** (인라인):
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "lambda:InvokeFunction",
    "Resource": [
      "arn:aws:lambda:ap-northeast-2:<ACCOUNT>:function:portfolio-mvp-run_screening:*",
      "arn:aws:lambda:ap-northeast-2:<ACCOUNT>:function:portfolio-mvp-agent_bullbear_bull:*",
      "arn:aws:lambda:ap-northeast-2:<ACCOUNT>:function:portfolio-mvp-agent_bullbear_bear:*",
      "arn:aws:lambda:ap-northeast-2:<ACCOUNT>:function:portfolio-mvp-agent_scenario:*"
    ]
  }]
}
```
> 워크플로우가 호출하는 모든 Lambda 를 나열한다 — M1 `run_screening`, M2 BullBearMap 의
> `agent_bullbear_{bull,bear}`, M3 ScenarioMap 의 `agent_scenario`. 신규 state 추가 시 갱신.

### 3) EventBridge 호출 역할 (`portfolio-mvp-eventbridge-role`)

**신뢰 정책**:
```json
{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "events.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}
```

**권한** (인라인):
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "states:StartExecution",
    "Resource": "arn:aws:states:ap-northeast-2:<ACCOUNT>:stateMachine:portfolio-mvp-screening"
  }]
}
```

### 4) CI/CD 배포 역할 (`AWS_DEPLOY_ROLE_ARN`) — 기존 OIDC role 에 권한 추가

GitHub Actions 가 Lambda/Step Functions 를 배포할 때 OIDC 로 assume 하는 role.
`secrets.AWS_DEPLOY_ROLE_ARN` 에 ARN 등록되어 있고, `deploy-lambdas.yml` 가 이미 사용 중.
Step Functions 자동 배포를 위해 **다음 권한을 인라인으로 추가** (한 번만):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ManageStateMachine",
      "Effect": "Allow",
      "Action": [
        "states:CreateStateMachine",
        "states:UpdateStateMachine",
        "states:ListStateMachines",
        "states:DescribeStateMachine"
      ],
      "Resource": "*"
    },
    {
      "Sid": "PassStepFunctionsExecRole",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::<ACCOUNT>:role/portfolio-mvp-step-functions-role",
      "Condition": {
        "StringEquals": {"iam:PassedToService": "states.amazonaws.com"}
      }
    }
  ]
}
```

**주의**:
- `iam:PassRole` 은 condition 으로 `states.amazonaws.com` 으로 제한 — Step Functions 외 서비스로
  넘기는 것 차단
- `states:*` 는 `Resource: "*"` 인데, list/describe 는 리소스 스코프 안 됨. 필요 시 create/update 만
  특정 ARN 으로 좁힐 수 있음 (`arn:aws:states:ap-northeast-2:<ACCOUNT>:stateMachine:portfolio-mvp-*`)

## GitHub Actions 변수 (vars)

리포지토리 Settings → Variables 에서 (모두 선택 — 미설정 시 기본값 사용):

| 변수 이름 | 기본값 | 사용처 |
|---|---|---|
| `LAMBDA_NAME_PREFIX` | `portfolio-mvp` | 두 워크플로우 모두 — Lambda/state machine 이름 prefix |
| `STATE_MACHINE_NAME` | `portfolio-mvp-screening` | deploy-step-functions.yml |
| `STEP_FUNCTIONS_ROLE_NAME` | `portfolio-mvp-step-functions-role` | deploy-step-functions.yml |

## Lambda 환경변수 (M1)

`portfolio-mvp-run_screening` Lambda 콘솔에서 설정:

| 키 | 값 | 비고 |
|---|---|---|
| `S3_BUCKET` | (실제 버킷명) | 필수 |
| `FMP_SECRET_ID` | (Secrets Manager 시크릿 이름) | 필수 |
| `LOG_LEVEL` | `INFO` | 선택 |
| `CACHE_MAX_AGE_DAYS` | `90` | 선택 (CLAUDE.md 분기 캐시 정책) |

**Memory**: 1024 MB, **Timeout**: 15분 권장 (캐시 미스 첫 주 5~7분 + 마진).

> **Timeout 주의**: `update_constituents` 는 콘솔 기본값 3초로 생성되어 있었는데,
> 번들 증가로 pyarrow 콜드스타트가 3초를 넘기면서 2026-05-15 부터 매주 전 재시도
> 실패(Sandbox.Timedout)했다 (`current.parquet` 이 존재해 스크리닝은 stale 데이터로
> 계속 성공 → 무증상). 2026-07-10 에 **120초로 상향**. 함수 생성 시 timeout 을
> 기본값으로 두지 말 것 — `update_constituents` 120s / `update_ohlcv` 900s.

## LLM Lambda (bullbear / scenario) 생성 설정

M1 의 `run_screening` 과 달리 LLM 에이전트 Lambda (`agent_bullbear_bull`,
`agent_bullbear_bear`, `agent_scenario`) 는 **Anthropic 시크릿이 추가로 필요**하다.
세 함수는 role·env·런타임 설정이 동일하므로 **하나를 만들면 나머지는 그대로 복사**
한다.

> **배포 패키지 크기 (50MB 한계)**: `update-function-code --zip-file` 직접 업로드는
> **zip 50 MiB(52,428,800 bytes)** 한계. pyarrow(~132MB)가 번들의 대부분이라 한계에
> 근접 → `deploy_lambda.sh` 가 빌드 시 pyarrow 의 미사용 컴포넌트(Flight, C++ include)
> 를 제거해 ~42MB 로 슬림화. **주의: 삭제 가능 여부는 Python lazy import 가 아니라
> ELF `DT_NEEDED` 기준** — `libarrow_substrait.so` 는 `pyarrow.lib`(core) 와
> `libarrow_python.so` 의 링크타임 의존성이라 삭제하면 모든 pyarrow import 가
> `Runtime.ImportModuleError` 로 즉사한다 (2026-06-29/07-06 주간 스크리닝·일간 OHLCV
> 장애 원인 — Cython `_substrait.so` 만 lazy). `deploy_lambda.sh` 에 core 필수 .so
> 존재 가드 있음.
>
> **향후 번들이 다시 50MB 근접 시 (M4 optimizer 의존성 추가 등): 컨테이너 이미지
> 전환이 1순위 방침** (2026-07-10 결정, 2주 장애 회고). 근거 —
> - 10GB 이미지 한계 → 슬림화·가드 코드 자체가 불필요 (근본 원인 제거)
> - Amazon Linux 내 빌드 → `pip --platform manylinux2014` 크로스 설치 핵 제거
> - 빌드 시점 로컬 스모크 (`docker run <image> python -c "import pyarrow"`) 로
>   import 깨짐을 배포 전에 검출 — 로컬 = 런타임 100% 동일 환경
> - 이미지 1개 + 함수별 CMD(핸들러) 오버라이드로 6개 함수 커버 (빌드·검증 1회)
> - 대가: Zip↔Image 패키지 타입 전환 불가 → 6개 함수 재생성 + Step Functions
>   ARN·EventBridge 타깃·env/role 재연결 필요 (반나절 마이그레이션), ECR + CI
>   docker build/push 추가. ECR 비용 ~$0.1/GB/월.
>
> 차선책 (소규모 초과에 임시 대응): (a) S3 경유 업로드(`--s3-bucket`/`--s3-key`,
> 250MB unzipped 한계 잔존 — CI role 에 s3:Put/GetObject 필요), (b) 추가 슬림화
> (`readelf -d` 로 DT_NEEDED 확인 필수), (c) 무거운 dep 를 Lambda Layer 로 분리.
> 람다별 개별 패키징은 pyarrow 가 전 람다 공통(`common/s3_io.py`)이라 효과 없음
> — 검토 후 기각 (2026-07-10).

### 공통 설정

| 옵션 | 값 | 비고 |
|---|---|---|
| Runtime | `python3.12` | `deploy_lambda.sh` 전제 |
| Architecture | **`x86_64`** | 스크립트가 amazonlinux2023 x86_64 wheel 설치 (pyarrow) — 필수 일치 |
| Handler | `lambdas.<dir>.handler.lambda_handler` | 예: `lambdas.agent_scenario.handler.lambda_handler` |
| Memory | `1024` MB | |
| Timeout | `900` s (15분) | LLM 사다리 + 캐시미스 마진 |
| Role | LLM Lambda 실행 역할 (아래) | |

### 실행 역할 — M1 role + Anthropic 시크릿

M1 `portfolio-mvp-run_screening-role` 의 권한(S3 `/*` + CloudWatch + FMP 시크릿)에
**Anthropic 시크릿 GetSecretValue 를 추가**한 role 을 LLM Lambda 가 공유한다.
인라인 정책의 Secrets 문(§4-1) 을 다음처럼 확장:

```json
{
  "Effect": "Allow",
  "Action": "secretsmanager:GetSecretValue",
  "Resource": [
    "arn:aws:secretsmanager:ap-northeast-2:<ACCOUNT>:secret:<FMP_SECRET_ID>-*",
    "arn:aws:secretsmanager:ap-northeast-2:<ACCOUNT>:secret:<ANTHROPIC_SECRET_ID>-*"
  ]
}
```

S3 정책은 `arn:aws:s3:::<S3_BUCKET>/*` (전 객체) 이므로 scenario 가 새로 쓰는
`scenarios/`·`expected_returns/`·`scenario_contexts/` 와 읽는 `agents/bullbear/`·
`ohlcv/`·income 캐시가 *이미 커버*된다 — S3 권한 추가 불필요.

### 환경변수

| 키 | 값 | 비고 |
|---|---|---|
| `S3_BUCKET` | (실제 버킷명) | 필수 |
| `FMP_SECRET_ID` | (FMP 시크릿 이름) | 필수 (분기 income cache-aside, ttm_eps) |
| `ANTHROPIC_SECRET_ID` | (Anthropic 시크릿 이름) | **필수 (LLM 호출)** |
| `LOG_LEVEL` | `INFO` | 선택 |
| `CACHE_MAX_AGE_DAYS` | `90` | 선택 |

> scenario 의 추가 prefix (`BULLBEAR_PREFIX`/`SCENARIOS_PREFIX` 등) 와 가격 config
> override (`SCENARIO_*`, docs §4.3) 는 코드 기본값이 운영값과 일치하므로 평소엔
> 설정 불필요. 비상 보수화 시에만 `SCENARIO_BULL_AGGRESSIVENESS` 등 추가.

### 신규 함수 생성 (한 번만)

`deploy_lambda.sh` 는 *기존 함수 갱신(UpdateFunctionCode)* 만 한다 — 신규 함수는
콘솔 또는 아래 CLI 로 한 번 생성해야 GitHub Actions 가 이후 코드만 갱신한다
(미생성 시 `ResourceNotFoundException`).

```bash
cd portfolio-mvp
REGION=ap-northeast-2

# 1) 기존 LLM Lambda 의 role/env 복사 (동일 설정 유지)
ROLE=$(aws lambda get-function-configuration --region $REGION \
  --function-name portfolio-mvp-agent_bullbear_bull --query 'Role' --output text)
aws lambda get-function-configuration --region $REGION \
  --function-name portfolio-mvp-agent_bullbear_bull --query 'Environment.Variables'

# 2) 패키징 (update 는 실패하지만 build/<dir>.zip 은 생성됨)
chmod +x scripts/deploy_lambda.sh
scripts/deploy_lambda.sh agent_scenario portfolio-mvp-agent_scenario || true

# 3) 함수 생성 (env 는 위 조회값으로 치환)
aws lambda create-function --region $REGION \
  --function-name portfolio-mvp-agent_scenario \
  --runtime python3.12 \
  --architectures x86_64 \
  --role "$ROLE" \
  --handler lambdas.agent_scenario.handler.lambda_handler \
  --timeout 900 --memory-size 1024 \
  --zip-file fileb://build/agent_scenario.zip \
  --environment 'Variables={S3_BUCKET=<버킷>,FMP_SECRET_ID=<...>,ANTHROPIC_SECRET_ID=<...>,LOG_LEVEL=INFO,CACHE_MAX_AGE_DAYS=90}'
```

생성 후 `deploy-lambdas` 워크플로우를 재실행하면 `UpdateFunctionCode` 가 성공한다.
**Step Functions 실행 역할**(`portfolio-mvp-step-functions-role`)에 신규 함수
`portfolio-mvp-agent_scenario` 의 `lambda:InvokeFunction` 권한도 추가해야 ScenarioMap
이 호출할 수 있다 (§4-2 role 의 Resource 목록에 추가).

## EventBridge 스케줄

스크립트 없이 AWS CLI 로 직접 (한 번만):

```bash
# 환경변수 설정
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION=ap-northeast-2
SM_ARN="arn:aws:states:${REGION}:${ACCOUNT}:stateMachine:portfolio-mvp-screening"
EB_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/portfolio-mvp-eventbridge-role"

# 스케줄 규칙 생성/갱신 — Mon 11:00 UTC = 06:00 EST / 07:00 EDT (둘 다 프리마켓)
aws events put-rule \
  --region "$REGION" \
  --name portfolio-mvp-weekly-screening \
  --description "Weekly screening trigger (CHARTER §2.4 Mon pre-market)" \
  --schedule-expression "cron(0 11 ? * MON *)" \
  --state ENABLED

# Step Functions 를 타깃으로 연결 — 빈 입력 ({}) 으로 호출 → Lambda 가 today(UTC) 사용
aws events put-targets \
  --region "$REGION" \
  --rule portfolio-mvp-weekly-screening \
  --targets "Id=1,Arn=${SM_ARN},RoleArn=${EB_ROLE_ARN},Input={}"
```

DST 변동 ±1시간은 프리마켓 안에서 흡수됨 (US 마켓 오픈 09:30 ET 보다 2.5~3.5h 이른 시점).

## 수동 실행 / 모니터링

### Step Functions 직접 실행
```bash
aws stepfunctions start-execution \
  --state-machine-arn "$SM_ARN" \
  --input '{"as_of_date": "2026-05-04"}'
```

`as_of_date` 미지정 시 Lambda 가 `today(UTC)` 사용.

### 실행 상태 확인
```bash
# 최근 실행 목록
aws stepfunctions list-executions --state-machine-arn "$SM_ARN" --max-results 5

# 특정 실행 상세 (output 포함)
aws stepfunctions describe-execution --execution-arn <execution-arn>
```

### Lambda 로그
```bash
aws logs tail /aws/lambda/portfolio-mvp-run_screening --follow
```

또는 CloudWatch Logs Insights 쿼리:
```
fields @timestamp, stage, n_current, selected_count, elapsed_seconds, n_fmp_failures
| filter stage = "completed"
| sort @timestamp desc
| limit 20
```

### S3 결과 확인
```bash
aws s3 ls s3://<S3_BUCKET>/screening/dt=2026-05-04/
aws s3 cp s3://<S3_BUCKET>/screening/dt=2026-05-04/result.json -
```

## 트러블슈팅

| 증상 | 원인/대응 |
|---|---|
| Lambda 가 `current.parquet 없음` 으로 실패 | `update_constituents` Lambda 를 한 번 실행해 bootstrap |
| `target_min(15) 미만` ValueError | universe filter 가 너무 많이 드롭. CloudWatch Logs 의 `n_with_history`, `n_with_metrics` 확인. FMP 캐시 퍼지 또는 임계값 일시 조정 |
| Step Functions 가 `States.Runtime` 으로 실패 | IAM role 의 lambda:InvokeFunction 권한 확인 |
| EventBridge 가 발화하지만 Step Functions 미실행 | EventBridge role 의 states:StartExecution 권한 확인 |
| Lambda 타임아웃 (15분 초과) | 분기 발표 시즌의 첫 실행이면 정상 (캐시 일괄 채움). 이후 주는 정상 시간 회복. 반복되면 Lambda 메모리 ↑ |

## 다음 (M2 예정)

- Step Functions 정의에 Bull/Bear Map state 추가 → `screening_workflow.asl.json` 만 갱신, 스크립트 동일
- 비용·실행시간 누적 데이터 후 SAM/CDK 이전 결정
