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
    "Resource": "arn:aws:lambda:ap-northeast-2:<ACCOUNT>:function:portfolio-mvp-run_screening:*"
  }]
}
```

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
