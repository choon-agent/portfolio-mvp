# CLAUDE.md

> Claude Code가 매 세션에서 자동으로 읽는 파일. 이 프로젝트에서 작업할 때 지켜야 할 규칙.
> 상위 의사결정 원칙은 `CHARTER.md` 참조 (충돌 시 CHARTER가 우선).

## 프로젝트 개요

LLM 에이전트 오케스트레이션을 활용한 주식 포트폴리오 관리 시스템 MVP.
목적 우선순위: LLM 학습 > 포트폴리오 산출물 > 퀀트 리서치 > 실제 돈 운용.
자세한 내용은 `CHARTER.md` 참조.

## 기술 스택

- **언어**: Python 3.12
- **클라우드**: AWS (리전: `ap-northeast-2`)
- **주요 서비스**: Lambda, S3, Athena, EventBridge, Step Functions
- **데이터 소스**: Financial Modeling Prep (FMP) API
- **LLM**: Anthropic Claude API (Sonnet 4.6 기본, Haiku 4.5 폴백)
- **IaC**: Plain ASL JSON + AWS CLI 스크립트 (M1 — `infra/step_functions/`, `scripts/deploy_step_functions.sh`). SAM/CDK 재검토는 M2 이후 워크플로우 복잡도 증가 시점.
- **테스트**: pytest (단위 테스트). 통합 테스트(moto/AWS 모킹)는 M2 이후 도입 예정.
- **LLM 응답 품질 평가**: DeepEval G-Eval (judge = Sonnet 4.6, 기본 3 criteria — `docs/02-bull-bear.md §11.5` baseline). PoC 단계는 로컬 pytest, M3+ Lambda 자동화 예정.
- **의존성 관리**: `requirements.txt` (Lambda 번들) + `requirements-dev.txt` (로컬 — pytest, boto3, deepeval) 분리

## 디렉토리 구조 (목표)

```
portfolio-mvp/
├── CHARTER.md              # 프로젝트 헌장 (변경 시 버전 업)
├── CLAUDE.md               # 이 파일
├── README.md               # 외부 독자용
├── docs/                   # 단계별 설계 문서
├── src/
│   ├── screening/          # 1단계: 코드 기반 스크리닝 (LLM X)
│   ├── agents/             # 2~3단계: LLM 에이전트 (Bull/Bear, Scenario)
│   ├── optimizer/          # 4단계: 포트폴리오 최적화 (LLM X)
│   ├── rebalancer/         # 5단계: 리밸런싱 오케스트레이터
│   └── common/             # 공용 유틸 (FMP 클라이언트, S3 I/O 등)
├── infra/                  # IaC 코드
├── tests/
└── scripts/                # 로컬 실행/디버깅용 스크립트
```

## 코딩 규칙

### 일반
- **비용 최우선**: LLM 호출 코드 작성 시 항상 예상 토큰/비용을 커밋 메시지에 기록
- LLM 호출은 반드시 `src/agents/` 하위에만 위치 (비용 추적·격리 용이)
- **순수 함수 우선**: I/O와 비즈니스 로직 분리 (테스트 용이성)
- 타입 힌트 필수 (`from __future__ import annotations` 사용)
- Pydantic으로 LLM 입출력 스키마 정의

### AWS
- 리전은 환경변수 `AWS_REGION` 또는 명시적 지정 (기본값 `ap-northeast-2`)
- boto3 클라이언트는 함수 모듈 레벨에서 생성 (Lambda 콜드스타트 최소화)
- **비밀키 금지**: 코드에 API 키·토큰 하드코딩 절대 금지. Secrets Manager 또는 환경변수만.

### FMP API
- 모든 호출은 캐싱 계층을 통해서만 (직접 호출 금지)
- 캐시 키는 `{endpoint}:{params_hash}` 형식
- 일간 데이터는 당일 캐시, 분기 재무는 90일 캐시

### LLM 에이전트
- 모델 선택 기본값: **Sonnet 4.6** (이유 없이 Opus 금지 — 비용)
- 프롬프트는 `src/agents/prompts/` 하위에 별도 파일로 분리
- 모든 LLM 호출은 다음을 로깅: `timestamp, model, input_tokens, output_tokens, cost_usd, purpose`
- 에이전트 출력은 Pydantic 모델로 검증 (JSON mode 활용)
- **비용 상한**: 월 $200 (Charter §2.2). 단일 에이전트 호출이 $1 초과 예상 시 중단하고 설계 재검토.
- **응답 품질 회귀 게이트**: Bull/Bear 프롬프트 (`src/agents/prompts/{bull,bear}_system.md`) 또는 평가 criteria (`src/agents/bull_bear/evaluation/criteria.py`) 수정 시 DeepEval 회귀 실행 후 커밋 — `pytest -m deepeval` 또는 `scripts/run_bullbear_deepeval.py` (judge 호출 비용 발생). baseline 은 `docs/02-bull-bear.md §11.5`.

## 커밋 컨벤션

- Conventional Commits 형식 사용
- `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `perf:`
- 메시지는 영어 또는 한국어 (프로젝트 전체 일관성 유지)
- **LLM 호출이 추가/변경되는 커밋은 반드시 예상 월 비용 영향을 본문에 기재**
  - 예: `feat(agents): add Bull/Bear debate agents\n\nEstimated cost impact: +$30/month (15 stocks × 2 agents × 4 runs)`

## 브랜치 전략

- `main`: 배포 가능한 상태 유지
- `feat/<name>`: 기능 브랜치
- `exp/<name>`: 실험 브랜치 (머지 안 할 수도 있음, 학습용)
- 페이퍼 트레이딩 단계(M1~M3)에서는 main에 직접 푸시 가능
- 실전 전환(M4~) 후에는 PR 필수

## 테스트 규칙

- 순수 함수는 단위 테스트 필수
- LLM 호출 함수는 **목 테스트**(Mock API response) 작성
- AWS 리소스 호출은 `moto`로 모킹
- 통합 테스트는 `tests/integration/`에 분리 (CI에서 별도 실행)
- 마커 분리:
  - `golden` — 실제 LLM 호출 없이 저장된 스냅샷 JSON 검증 (CI 기본 제외)
  - `deepeval` — judge LLM 호출 발생하는 응답 품질 평가 (CI 기본 제외, `ANTHROPIC_API_KEY` 필요)

## Claude Code 작업 규칙

### 하지 말 것
- `main` 브랜치에 `rm -rf`, `git reset --hard` 같은 파괴적 명령 실행 금지
- 승인 없이 `git push --force` 금지
- API 키를 리터럴로 코드에 작성 금지
- `requirements.txt` / `pyproject.toml` 변경 시 이유를 커밋 메시지에 명시

### 권장
- 2파일 이상 수정하는 작업은 `/plan` 모드 먼저 사용
- 커밋 전 `ruff check` + `pytest` 자동 실행
- 긴 작업은 중간에 커밋 자주 (롤백 용이)
- 불확실하면 묻기 (특히 비용·보안 관련)

## 참고 문서

- 프로젝트 헌장: `CHARTER.md`
- 단계별 설계: `docs/01-screening.md` ~ `docs/05-rebalancing.md` (작성 중)
- 외부: FMP API 문서, Anthropic API 문서, AWS Lambda 문서

## 현재 단계 (M3 — 2026-05-31 기준)

- [x] M0 기반 (Charter / Repo / CLAUDE.md / README / 스캐폴딩 / FMP 캐싱 계층)
- [x] **M1 — 1단계 스크리닝 (코드 기반) — 완료·운영 중**
  - 7개 모듈 + 154개 단위 테스트. `run_screening` Lambda + Step Functions +
    EventBridge (Mon 06:00 ET). universe 483 → selected 20. 설계: `docs/01-screening.md`
- [x] **M2 — 2단계 Bull/Bear — 완료·운영 중**
  - `agents/bull_bear/` (schemas/mappers/context_builder/agent/anthropic_adapter/
    lambda_core) + `agent_bullbear_{bull,bear}` Lambda. 4주 누적 운영 ($2.76/월,
    160 invoke 100% 성공, retry 0). 설계: `docs/02-bull-bear.md`
- [~] **M3 — 3단계 시나리오 모델링 (옵션 C) — 구현 #1~#10 완료, 운영 대기**
  - 설계 `docs/03-scenario.md` v0.13 (§2.4/§4/§5/§6/§7/§10~§12 검토 박제)
  - 코드 `agents/scenario/` (schemas/pricing_config/pricing/context_builder/
    prompts/agent/trigger_evaluator/lambda_core) + `agent_scenario` Lambda +
    ASL ScenarioMap (G1 BullBearMap 체이닝 교정 포함). 시나리오 테스트 161건
  - 골든 4종목 실제 호출 검증 ($0.072). LLM 은 확률·narrative·트리거만, 가격은
    결정적 산식 (LLM ≠ 가격 산정 분리)
  - **남은 작업**: #11 AWS 배포 + 20종목 첫 운영 / #12 sensitivity 로깅 /
    #13 트리거 자동검증 batch / #14 DeepEval baseline (M3 후반·5주차, §11 참조)

미해결 디자인 채무: M1 은 `docs/01-screening.md §10`, M3 은 `docs/03-scenario.md
§12` (4그룹 — 즉시 결정 가능 4 / 데이터 게이트 12 / v2 3) 참조.