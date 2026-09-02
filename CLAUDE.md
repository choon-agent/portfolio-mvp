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
- **IaC**: Plain ASL JSON + AWS CLI 스크립트 (`infra/step_functions/`, `scripts/deploy_step_functions.sh`). Lambda 는 zip(`deploy_lambda.sh`) + **컨테이너 이미지**(`deploy_lambda_container.sh` — run_optimizer, numpy/scipy 계열이 zip 50MB 초과. infra/README 참조). SAM/CDK 재검토는 보류 중.
- **테스트**: pytest (단위 테스트). 통합 테스트(moto/AWS 모킹)는 M2 이후 도입 예정.
- **LLM 응답 품질 평가**: DeepEval G-Eval (judge = Sonnet 4.6, 기본 3 criteria — `docs/02-bull-bear.md §11.5` baseline). PoC 단계는 로컬 pytest, M3+ Lambda 자동화 예정.
- **의존성 관리**: `requirements.txt` (zip Lambda 번들) + `requirements-dev.txt` (로컬 — pytest, boto3, deepeval, PyPortfolioOpt) + `infra/docker/optimizer-requirements.txt` (컨테이너 전용) 분리

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

## 현재 단계 (M3 후반 + 4·5단계 운영 — 2026-09-03 기준)

- [x] M0 기반 (Charter / Repo / CLAUDE.md / README / 스캐폴딩 / FMP 캐싱 계층)
- [x] **M1 — 1단계 스크리닝 (코드 기반) — 완료·운영 중**
  - 7개 모듈 + 단위 테스트. `run_screening` Lambda + Step Functions +
    EventBridge (Mon 06:00 ET). universe ~503 → selected 20. 설계: `docs/01-screening.md`
- [x] **M2 — 2단계 Bull/Bear — 완료·운영 중**
  - `agents/bull_bear/` + `agent_bullbear_{bull,bear}` Lambda. 설계: `docs/02-bull-bear.md`
- [~] **M3 — 3단계 시나리오 모델링 (옵션 C) — 구현 #1~#13(PoC) 완료·자동 운영 중**
  - 설계 `docs/03-scenario.md` **v0.17** / 4주 운영 회고 합격 (`03-scenario-retro.md §0.6`)
  - 코드 `agents/scenario/` + `agent_scenario` Lambda + ASL ScenarioMap.
    LLM 은 확률·narrative·트리거만, 가격은 결정적 산식 + base·bear 현재가 cap (v0.17)
  - 주요 이력 (상세 retro §0.5/§0.7): 6/29~7/6 2주 결번(pyarrow 배포 사고, 302f3cb 복구) /
    epsDiluted 필드 버그로 07-13 이전 데이터 구 regime (90afd8b 수정 — **유효 데이터는
    2026-07-14 부터**, 12주 판정 ~10월 초) / bear cap v0.16→v0.17 primary 승격 (4b9ca19)
  - #13 트리거 자동검증: 로컬 배치 가동 (`scripts/run_trigger_batch.py` — 주 1회 수동
    실행, `--upload`. S3 `trigger_evaluations/` 누적, observe-only)
  - **남은 작업**: #13 Lambda 자동화 결정 (§12.2 D) / #14 DeepEval baseline /
    §12.3 (d) 극소 EPS 가드 (빈도 관찰 중)
- [x] **4단계 최적화 — 구현 완료·자동 운영 편입 (2026-08-17)**
  - `docs/04-optimizer.md` v0.3 / `src/optimizer/` 5모듈 + `run_optimizer` **컨테이너
    Lambda** (ECR, 컨테이너 방침 첫 적용) / ASL RunOptimizer state (2026-08-17
    정기 실행부터 1~4단계 자동). 옵션 B baseline 병렬 산출 (§1.4.2 #3)
- [x] **5단계 리밸런싱 — 구현 완료·배포 (2026-09-03)**
  - `docs/05-rebalancing.md` v0.3 / `src/rebalancer/` 4모듈 + `run_rebalancer`
    전용 컨테이너 Lambda + ASL RunRebalancer (09-07 정기 실행부터 **1~5단계 자동**).
    페이퍼 계좌 2개 병렬 (primary + option_b — §1.4.2 #3 실현수익률 트랙),
    08-17 백필 씨딩 완료. no-trade band 1.5%p + 범위 밖 면제, SPY 벤치마크 수집

미해결 디자인 채무: M1 은 `docs/01-screening.md §10`, M3 은 `docs/03-scenario.md
§12` (12.2 잔여 1 [~D] / 12.3 데이터 게이트 잔여 / 12.4 v2 3) 참조.
**M3 말 재검토 안건(~10월 초)은 `docs/03-scenario-retro.md §0.8` 로 일원화** —
12주 판정 + Charter 감사 이월(백테스트 엔진·블로그·패턴 비교표·Athena 정리) 포함.