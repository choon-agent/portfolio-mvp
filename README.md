# portfolio-mvp

> LLM 에이전트 오케스트레이션 학습을 위한 주식 포트폴리오 관리 시스템.
> S&P 500 유니버스, 주 1회 리밸런싱, Bull/Bear 에이전트 기반 종목 리서치.

**기간**: 2026-04-20 ~ 2026-10-20 (6개월 MVP)
**현재 단계**: **M3 운영 중 — 1·2·3단계 자동 운영 (스크리닝 + Bull/Bear + 시나리오 모델링). 4~5단계(최적화·리밸런싱) 다음**

---

## 이 프로젝트가 존재하는 이유

목적 우선순위 (충돌 시 상위 목적 우선):

1. **LLM 에이전트 오케스트레이션 학습** — 다양한 에이전트 패턴 실험
2. **포트폴리오용 산출물** — 재현 가능한 코드와 의사결정 로그
3. **퀀트 리서치 플레이그라운드** — 팩터·전략 실험
4. **실제 돈 운용** — $5k~$20k 학습비용으로 간주

상세 원칙과 제약은 [CHARTER.md](CHARTER.md) 참조. 개발 규칙은 [CLAUDE.md](CLAUDE.md) 참조.

---

## 시스템 개요

주간 실행 파이프라인 (5단계):

```
[1] 스크리닝          ✅ 운영 — 코드 기반 팩터 스코어 (LLM 사용 X)
       ↓
[2] Bull/Bear 리서치  ✅ 운영 — 종목당 LLM 에이전트 2개 (핵심 학습 포인트)
       ↓
[3] 시나리오 모델링   ✅ 운영 (M3) — LLM 은 narrative·확률·트리거만, 가격은 결정적 산식
       ↓
[4] 포트폴리오 최적화  ⏳ 다음 — PyPortfolioOpt 등 (LLM 사용 X)
       ↓
[5] 리밸런싱          ⏳ 다음 — 룰 기반 매매, LLM 은 근거 생성만
```

유니버스 10~15 종목, 섹터당 ≤35%, 월 LLM 비용 상한 $200.

**현재 운영 상태** (M3 운영 중, 2026-06-09):
- 매주 월 06:00 ET (EventBridge cron) → Step Functions `RunScreening` → `BullBearMap` (Bull/Bear Parallel) → `ScenarioMap` (시나리오) → S3 저장
- 시나리오 단계 자동 운영 2주 누적: 매주 20/20 성공, 재시도 0%, 주간 비용 **$0.36** (20×$0.018), data_quality_flags 0
- 결정성 정책 100% (Bull/Bear `context_input_hash`, 시나리오 `scenario_input_hash` — 동일 입력 재호출 시 LLM 호출 0회)
- 옵션 C: LLM ≠ 가격 산정 분리 — LLM 은 확률·narrative·무효화 트리거만, scenario_prices 는 결정적 산식
- 관찰: 종목 turnover ~25% (주간 캐시 무효 → 풀 비용), expected_return 음수 skew(보수 config) — 4주 회고에서 config 조정 판단 (`docs/03-scenario-retro.md`)

---

## 기술 스택

| 영역 | 도구 |
|---|---|
| 언어 | Python 3.12 |
| 클라우드 | AWS (`ap-northeast-2`) — Lambda, S3, EventBridge, Athena, Step Functions |
| 데이터 | Financial Modeling Prep (FMP) API + Wikipedia (S&P 500 구성종목) |
| LLM | Anthropic Claude (Sonnet 4.6 기본, Haiku 4.5 폴백) |
| 저장 포맷 | Parquet (S3) |
| 배포 | GitHub Actions + OIDC → Lambda direct upload |
| 스키마 검증 | Pydantic |
| 응답 품질 평가 | DeepEval G-Eval (judge = Sonnet 4.6, 3 criteria — Bull/Bear hard rule 회귀 가드) |

---

## 디렉토리 구조

```
portfolio-mvp/
├── CHARTER.md                       # 프로젝트 헌장 (상위 의사결정 원칙)
├── CLAUDE.md                        # 개발 규칙 (Claude Code 자동 로드)
├── README.md                        # 이 파일
├── requirements.txt                 # Lambda 번들용 (pydantic·pyarrow·requests·bs4·anthropic)
├── requirements-dev.txt             # 로컬 개발용 (pytest, boto3, deepeval)
├── docs/
│   ├── 01-screening.md              # 1단계 스크리닝 설계 (M1 운영 중)
│   ├── 02-bull-bear.md              # 2단계 Bull/Bear 설계 (v0.8, M2 운영 중)
│   ├── 03-scenario.md               # 3단계 시나리오 설계 (v0.14, M3 운영 중)
│   └── 03-scenario-retro.md         # M3 4주 운영 회고 프롬프트 + 체크리스트 + 주차별 운영 로그
├── infra/
│   ├── README.md                    # 배포 순서, IAM 정책, EventBridge 명령, LLM Lambda 생성 설정
│   └── step_functions/
│       └── screening_workflow.asl.json  # RunScreening + BullBearMap + ScenarioMap (M1 + M2 + M3)
├── src/
│   ├── common/
│   │   ├── fmp_client.py            # FMP 클라이언트 (OHLCV / key-metrics-ttm / income·cashflow 분기)
│   │   ├── fundamentals.py          # cache-aside (key-metrics-ttm + 분기 statements, 90일 TTL)
│   │   ├── sp500_wikipedia.py       # S&P 500 구성종목 스크래퍼
│   │   ├── s3_io.py                 # S3/Secrets Manager I/O
│   │   ├── ohlcv.py                 # OHLCV 수집·저장
│   │   └── models.py                # Pydantic 도메인 모델
│   ├── screening/                   # 1단계: 코드 기반 (LLM 미사용)
│   │   ├── schemas.py / constituents.py / universe.py / factors.py
│   │   ├── normalize.py / score.py / peer_context.py / pipeline.py
│   ├── agents/                      # 2·3단계: LLM 에이전트 (M2·M3)
│   │   ├── bull_bear/
│   │   │   ├── schemas.py           # StockContext, BullBearOpinion (평탄 1-depth)
│   │   │   ├── mappers.py           # ScreenedStock → StockContext 1:1 평탄화
│   │   │   ├── context_builder.py   # OHLCV/펀더멘털 → PriceSummary/Fundamentals + to_prompt_markdown 화이트리스트
│   │   │   ├── agent.py             # 사다리 (Sonnet→retry→Haiku) + 검증 + context_input_hash
│   │   │   ├── anthropic_adapter.py # AnthropicCaller Protocol 구현
│   │   │   ├── lambda_core.py       # Lambda 공유 코어 (캐시 hit/miss 분기 + S3 저장)
│   │   │   └── evaluation/          # 응답 품질 평가 (DeepEval G-Eval — M2 종료 후 PoC)
│   │   │       ├── criteria.py      # G-Eval criteria (hard rule 1·3·4 인코딩, lazy import)
│   │   │       └── adapters.py      # AnthropicJudge + 골든 스냅샷 → LLMTestCase 변환
│   │   ├── scenario/                # 3단계: 시나리오 모델링 (M3 신규 — 옵션 C)
│   │   │   ├── schemas.py           # ScenarioContext / ScenarioOpinion / ExpectedReturn
│   │   │   ├── pricing_config.py    # ScenarioPricingConfig + 로더 (env/override, 가격 역전 검증)
│   │   │   ├── pricing.py           # 결정적 가격 산식 (bull/base/bear, percentile 손수 구현)
│   │   │   ├── context_builder.py   # Bull/Bear 의견 + OHLCV/펀더멘털 → ScenarioContext
│   │   │   ├── agent.py             # LLM 진입점 (narrative+확률+트리거만, 사다리 재사용)
│   │   │   ├── trigger_evaluator.py # 무효화 트리거 자동 검증 (observe-only, #13 활성화 대기)
│   │   │   └── lambda_core.py       # 캐시(scenario_input_hash) + compute_expected_return + S3 저장
│   │   └── prompts/
│   │       ├── bull_system.md       # Bull 시스템 프롬프트 (strict schema 섹션 포함)
│   │       ├── bear_system.md       # Bear 시스템 프롬프트 (미러 구조)
│   │       ├── bullbear_user.md     # 사용자 프롬프트 템플릿 (placeholder 4개)
│   │       ├── scenario_system.md   # 시나리오 시스템 프롬프트 (가격 미산정·트리거 규칙, M3)
│   │       └── scenario_user.md     # 시나리오 사용자 프롬프트 템플릿 (M3)
│   ├── lambdas/
│   │   ├── update_constituents/handler.py    # 주 1회 구성종목 업데이트
│   │   ├── update_ohlcv/handler.py           # 매 거래일 OHLCV 증분 업데이트
│   │   ├── run_screening/handler.py          # 매주 월 스크리닝 (M1)
│   │   ├── agent_bullbear_bull/handler.py    # thin wrapper, stance="bull" (M2 신규)
│   │   ├── agent_bullbear_bear/handler.py    # thin wrapper, stance="bear" (M2 신규)
│   │   └── agent_scenario/handler.py         # thin wrapper, 시나리오 (M3 신규 — single-stance)
│   ├── tests/                       # pytest 421 케이스 (M3) + DeepEval 24 케이스 (PoC)
│   └── pytest.ini                   # marker 등록 (golden, deepeval)
├── scripts/
│   ├── deploy_lambda.sh             # Lambda 패키징·배포 (agents/ 포함)
│   ├── deploy_step_functions.sh     # ASL placeholder 치환·배포
│   ├── backfill_ohlcv.py            # OHLCV 초기 백필
│   ├── dry_run_screening.py         # pipeline 로컬 검증
│   ├── probe_fmp_fundamentals.py    # FMP 엔드포인트 가용성 점검
│   ├── run_bullbear_golden.py       # Bull/Bear 골든 케이스 실행 (M2 — 4종목 fixture)
│   ├── run_bullbear_deepeval.py     # DeepEval 단발 실행 + 리포트 저장 (PoC)
│   └── run_scenario_golden.py       # 시나리오 골든 스냅샷 생성 (M3 — 4종목, 1회 $0.072)
├── tests/
│   └── golden/
│       ├── bullbear/                # 골든 스냅샷 (AAPL/XOM/NVDA/JPM × bull/bear, 8개)
│       │   └── reports/             # DeepEval 리포트 (deepeval_report.json — judge reasoning 포함)
│       └── scenario/                # 시나리오 골든 스냅샷 (AAPL/JPM/NVDA/XOM, M3)
└── .github/
    └── workflows/
        ├── deploy-lambdas.yml       # CI/CD: src/agents/** path 트리거 추가
        └── deploy-step-functions.yml
```

---

## 현재까지 진행된 작업

### ✅ 인프라 · CI/CD
- GitHub Actions → AWS Lambda 자동 배포 파이프라인 구축
- OIDC 인증 (org 공용 `choon-github-actions-role` 활용)
- 배포 스크립트가 `common/`, `screening/`, `lambdas/<name>/` 를 번들링하고 `requirements.txt` 기반 의존성 설치
- 변경 감지 로직: `src/lambdas/<name>/` 만 바뀌면 해당 Lambda 만, 공용 코드/의존성 바뀌면 전체 재배포
- 워크플로우 path filter 에 `requirements.txt` 포함

### ✅ 데이터 레이어
- FMP API 클라이언트 (stable API 기준, 재시도·백오프 포함) — OHLCV 수집 전용
- S3 I/O 유틸 (atomic write, append-only parquet 로그)
- Secrets Manager 연동 (FMP API 키)
- Pydantic 도메인 모델 (`Constituent`, `ConstituentChangeEvent`, `DiffResult`)

### ✅ 1단계 스크리닝 — 구성종목 관리
- **S&P 500 데이터 소스를 Wikipedia 로 전환**
  - FMP 현 구독 플랜이 `sp500-constituent` 엔드포인트를 커버하지 않음
  - Wikipedia 한 페이지에 현재 구성종목 + 변경 이력 테이블이 모두 존재하여 기존 인터페이스 그대로 재사용
- `update_constituents` Lambda 동작 확인 (bootstrap 실행 성공, 503 종목 `current.parquet` 저장)
- 구성종목 diff 로직: added/removed/metadata_changed 분리, 재편입 케이스 지원

### ✅ 1단계 스크리닝 — OHLCV 수집
- **초기 백필**: `scripts/backfill_ohlcv.py` 로 현재 구성종목 503개의 5년치 OHLCV 적재 완료 (로컬 일회성 실행, 약 7분 소요)
  - S3: `ohlcv/ticker={SYMBOL}/data.parquet` 구조
  - 실패·재시도·진행률 추정·resume 지원
- **Dual-class 티커 정규화**: `BRK.B` 같은 점 포함 티커는 FMP 호출 시 자동으로 하이픈(`BRK-B`)으로 변환, 저장은 점 유지
- **일일 증분 업데이트 Lambda** (`update_ohlcv`): 매 거래일 종료 후 실행
  - 각 심볼의 기존 parquet 마지막 날짜 이후 행만 append (덮어쓰기 X)
  - **FMP 의 5년 롤링 윈도우 밖 과거 데이터를 S3 에 영구 보존**하기 위한 설계
  - 개별 심볼 실패가 전체 실행을 중단시키지 않음 (로깅 + 계속 진행)

### ✅ 1단계 스크리닝 — 팩터 스코어링 파이프라인 (M1 완료)
- **순수 함수 모듈 7개** (`src/screening/`): schemas, universe, factors, normalize, score, peer_context, pipeline
  - I/O 와 비즈니스 로직 분리 (CLAUDE.md 원칙)
  - 154 단위 테스트 통과 (모든 결정 분기·폴백 검증)
- **유니버스 필터** ([§3.1](docs/01-screening.md)): 시총 ≥ $2B, 12개월 거래일 ≥ 250일, 거래대금 ≥ $20M, seasoning ≥ 365일
- **팩터 (모멘텀 + 밸류)** ([§3.2](docs/01-screening.md)):
  - 모멘텀: 12-1m + 6m (raw), 결합 후 sub_sector z-score
  - 밸류: P/E TTM (1/earningsYieldTTM 도출), EV/EBITDA, FCF Yield — 각각 z-score 후 부호 정규화 평균
- **3단 폴백 그룹화** ([§3.3](docs/01-screening.md)): sub_sector(≥5) → sector → universe
- **상위 선택** ([§3.5](docs/01-screening.md)): composite 내림차순, 클린 데이터 우선, target 15~20
- **peer_context 사전 조립** ([§3.6](docs/01-screening.md), 부록 A): 같은 sub_sector → 부족하면 sector 폴백 (singleton sub_sector 도 5개 보장)
- **`run_screening` Lambda** + **Step Functions** + **EventBridge (Mon 06:00 ET)** 자동 실행
- **첫 운영 실행 (2026-04-25 기준)**: universe 483 → selected 20, peer_context 평균 5.0, 0 FMP 실패, 150초 소요

### ✅ FMP 펀더멘털 캐싱 (M1 신규)
- **`key-metrics-ttm` 단일 엔드포인트**로 P/E·EV/EBITDA·FCF Yield + marketCap 모두 도출 (호출 수 절반)
- **Cache-aside 패턴** ([fundamentals.py](src/common/fundamentals.py)): S3 hit + fresh → 캐시, miss/stale → FMP → S3 저장
- 90일 TTL ([CLAUDE.md](CLAUDE.md) 분기 재무 캐시 규칙)
- **Graceful degradation**: FMP 실패 시 stale 캐시라도 반환 → CHARTER §4.1 "주간 리밸런싱 성공률 ≥90%" 우호

### ✅ EventBridge 스케줄
- `update_constituents` — 매주 월요일 ET 09:00 (프리마켓). 구성종목 diff 및 변경 이벤트 로깅
- `update_ohlcv` — 매 평일 ET 22:00 (장 마감 후 FMP EOD 반영 완료 시점)
- **`run_screening` Step Functions** — 매주 월요일 11:00 UTC (= 06:00 EST / 07:00 EDT, 둘 다 프리마켓) (M1 신규)
- 모두 Scheduler 트리거 → CloudWatch Logs 정상 확인 완료

### ✅ Step Functions / IaC (M1)
- **워크플로우 정의**: [`infra/step_functions/screening_workflow.asl.json`](infra/step_functions/screening_workflow.asl.json) — Plain ASL JSON + placeholder 치환식
- **배포 자동화**: [`scripts/deploy_step_functions.sh`](scripts/deploy_step_functions.sh) (멱등 create-or-update) + [GitHub Actions 워크플로우](.github/workflows/deploy-step-functions.yml)
- **재시도 정책**: Lambda.ServiceException 외 4종 트랜지언트 에러에 대해 5초 백오프 × 3회
- **운영 가이드**: [`infra/README.md`](infra/README.md) — IAM 4개 역할 정책, EventBridge CLI 명령, 트러블슈팅

### ✅ 2단계 Bull/Bear 에이전트 (M2 종료)

**구현 완료** ([docs/02-bull-bear.md v0.8](docs/02-bull-bear.md)):
- **평탄화 `StockContext`** — `ScreenedStock` 임베드 대신 1-depth 명시 필드 (스크리닝-Bull/Bear 결합도 ↓, 입력 토큰 효율 ↑)
- **사다리 (Sonnet primary → primary retry → Haiku fallback)** — JSON 파싱·schema·네트워크 실패 모두 흡수
- **결정성 정책** — `temperature=0` + `context_input_hash(StockContext)` SHA-256으로 같은 입력 재호출 시 LLM 호출 생략 (운영 cache hit 100% 검증)
- **`MaxConcurrency=1`** — Anthropic 8K tok/min 한도 회피 (산정 공식: `MaxConcurrency × 2 stance × max_tokens ≤ 한도`)
- **종목별 Catch 격리** — `BullBearParallel.Catch[States.ALL]` → `RecordItemFailure` Pass state, 한 종목 실패가 Map 전체 중단 안 시킴

**검증 완료**:
- **골든 케이스 4종목** (AAPL/XOM/NVDA/JPM, sector 다양성 — 비용 $0.144) — 응답 품질 baseline
- **운영 첫 dry-run** (2026-04-30, 20종목): 40 invoke, retry 0회, **총 비용 $0.083**, cache hit 87.5%
- **응답 품질 인간 검토** (5 sub-sector sample): 5 hard rule 모두 통과, sector 보강 효과 6 sub-sector 최종 검증
- **응답 품질 자동 평가 baseline** (2026-05-24 PoC — [docs/02-bull-bear.md §11.5](docs/02-bull-bear.md)): DeepEval G-Eval, judge = Sonnet 4.6, 골든 8건 × 3 criteria (`evidence_grounded` / `risks_are_company_specific` / `signals_not_primary_evidence`) **24 판정 전수 통과** (비용 $0.37). hard rule #2 "No recommendations" 는 기존 정규식 가드가 결정적으로 차단해 평가 셋에서 제외. 리포트: [`tests/golden/bullbear/reports/deepeval_report.json`](tests/golden/bullbear/reports/deepeval_report.json)
- **332 단위 테스트 + 48 골든 회귀 가드 + 24 DeepEval 평가**

### ✅ Bull/Bear 인프라 (M2)
- **두 Lambda** (`agent_bullbear_bull`, `agent_bullbear_bear`) — 공유 `lambda_core.handle` + thin wrapper로 stance만 다르게 주입
- **Step Functions ASL 확장** — `RunScreening` → `BullBearMap` (Map MaxConcurrency=1, ItemSelector로 lineage 매핑) → 안 `BullBearParallel` (Bull/Bear 동시) → S3 저장
- **deploy 인프라 갱신** — `deploy_lambda.sh`가 `agents/` 패키징 (lazy `__init__.py`), `.github/workflows/deploy-lambdas.yml`이 `src/agents/**` path 트리거. `deploy_step_functions.sh`는 placeholder 치환
- **EventBridge 정기 트리거 연동** — 매주 월 06:00 ET cron으로 자동 운영 진입

### ✅ 3단계 시나리오 모델링 (M3 — 운영 중)

**설계** ([docs/03-scenario.md v0.14](docs/03-scenario.md), §2.4/§4/§5/§6/§7/§10~§12 검토 박제):
- **옵션 C — LLM ≠ 가격 산정 분리**: LLM 은 narrative + 시나리오 확률(bull/base/bear) + 무효화 트리거만 생성, `scenario_prices` 는 결정적 산식(`pricing.py`)으로 계산. 할루시네이션을 가격에서 차단 (CHARTER §6 정합)
- **결정적 가격 산식** — bull(percentile)/base(peer P/E·cap)/bear 를 historical·peer·52w fallback 사다리로 산출, config(`ScenarioPricingConfig`)로 공격성 조정. percentile 은 numpy 의존 없이 손수 linear interp (콜드스타트 최소)
- **`scenario_input_hash` 결정성 캐시** — M2 `context_input_hash` 패턴 재사용, 같은 입력 재호출 시 LLM 호출 생략 (재실행 폭주 방지 + 재현성)
- **무효화 트리거 자동 검증** (`trigger_evaluator.py`) — observe-only, 회고 calibration 전용 (라이브 매매 피드백 없음). batch 활성화는 #13(분기 발표 시즌)

**구현 #1~#10 완료**:
- `agents/scenario/` (schemas / pricing_config / pricing / context_builder / agent / trigger_evaluator / lambda_core) + `agent_scenario` Lambda + ASL `ScenarioMap`
- ASL G1 체이닝 교정 — `BullBearMap` 에 `Next:ScenarioMap` + `ResultPath:"$.bullbear_results"` 보존 (Map 출력이 `$.result` 덮어쓰지 않도록)
- **골든 4종목 실제 호출 검증** (AAPL/JPM/NVDA/XOM, 1회 $0.072) — 저장 `ScenarioOpinion` → `compute_expected_return` 재실행 시 `ExpectedReturn` 결정적 정확 재현(exact assert)

**운영** (2026-06-01 첫 자동 스케줄 이후 2주 누적):
- 매주 20/20 성공, **재시도 0%**, 주간 비용 **$0.36**(20×$0.018, 예측 정합), data_quality_flags 0
- 첫 수동 운영(2026-05-31)에서 narrative 300자 한계로 3종목(CRL/WTW/EIX) 실패 → v0.14 max_length 300→500 완화 후 20/20
- 관찰: expected_return 음수 skew(16→17/20, 보수 config), 종목 turnover ~25%(주간 캐시 무효) → 4주 회고에서 config·hysteresis 판단 ([docs/03-scenario-retro.md](docs/03-scenario-retro.md))

**남은 작업**: #11 첫 운영 안정화 / #12 sensitivity 로깅 / #13 트리거 자동검증 batch / #14 DeepEval baseline (M3 후반·5주차, §11 참조)

### ✅ 문서화
- CHARTER (헌장): 우선순위·제약·성공 기준 확정
- CLAUDE.md (개발 규칙): 코딩 규칙, 커밋 컨벤션, 비용 상한
- [docs/01-screening.md](docs/01-screening.md): 1단계 설계 (M1 운영 중)
- [docs/02-bull-bear.md](docs/02-bull-bear.md) **v0.8**: 2단계 설계 (M2 운영 중) — 운영 모니터링 정책 §11 + 4주 누적 평가 포함
- [docs/03-scenario.md](docs/03-scenario.md) **v0.14**: 3단계 설계 (M3 운영 중) — 옵션 C, 검토 박제 §2.4~§12
- [docs/03-scenario-retro.md](docs/03-scenario-retro.md): M3 4주 운영 회고 프롬프트 + 체크리스트 + 주차별 운영 로그
- [infra/README.md](infra/README.md): AWS 인프라 배포·운영 가이드 (LLM Lambda 생성 설정 포함)

---

## 앞으로 진행할 작업

### M1 (✅ 완료 — 2026-04-28)
1. 1단계 스크리닝 자동화

### M2 (✅ 완료 — 2026-04-30)
2. 2단계 Bull/Bear 에이전트 — 운영 진입
3. Step Functions ASL 확장 (`BullBearMap`)
4. EventBridge 정기 트리거 연결

### M3 (3개월차, Phase 1 종료 — 진행 중)
5. ✅ **3단계 시나리오 모델링** — 구현 #1~#10 완료, 자동 운영 중 (위 "진행된 작업" 참조)
6. ⏳ **M3 후반 잔여** — #12 sensitivity 로깅 / #13 트리거 자동검증 batch(분기 발표 시즌) / #14 DeepEval baseline + 4주 운영 회고([docs/03-scenario-retro.md](docs/03-scenario-retro.md))
7. ⏳ **4~5단계 연결** — 포트폴리오 최적화 (PyPortfolioOpt) + 리밸런싱 (룰 기반)
8. ⏳ **페이퍼 트레이딩 첫 주간 실행 성공** — 5단계 모두 연결된 첫 end-to-end
9. ⏳ **Charter 재검토 및 실전 전환 판단** — [CHARTER.md §4.1](CHARTER.md) 4개 기준 충족 시 소액 실전 전환
10. ⏳ **블로그 1편 초고**

### 운영 안정화 항목 (4주 누적 시점 평가)
- **M2 Bull/Bear 4주 평가 완료** ([docs/02-bull-bear.md v0.8](docs/02-bull-bear.md)): $2.76/월, 160 invoke 100% 성공, retry/fallback 0
- **종목 풀 turnover** — 너무 높으면 의견 캐싱 2주 TTL 또는 스크리닝 hysteresis 도입 ([docs/02-bull-bear.md §10](docs/02-bull-bear.md), M3 §1 §10 연결). M3 Week 2에서 ~25% 관찰 → 4주 회고 판단
- **사다리 retry/fallback 빈도** — 5% 이상이면 Anthropic 한도 상향 신청 또는 max_tokens 조정
- **응답 품질 회귀** — DeepEval G-Eval 자동 평가 ([scripts/run_bullbear_deepeval.py](scripts/run_bullbear_deepeval.py), baseline §11.5) 가 1차 가드. criterion fail 시 judge reasoning 검토 → 동일 패턴 2건 이상이면 시스템 프롬프트 보강. 분기별 인간 sector sample 검토 병행
- **M3 시나리오 4주 회고** — config(음수 skew) / `peer_announcement` 정책 / `_validate_price_order` 제거 / narrative 500 충분성 ([docs/03-scenario-retro.md](docs/03-scenario-retro.md) §3 체크리스트)
- 상세 모니터링 정책: [docs/02-bull-bear.md §11](docs/02-bull-bear.md), [docs/03-scenario.md §11~§12](docs/03-scenario.md)

---

## 주의사항 및 제약

운영·데이터 레이어에 알려진 제약과 그 의미. 새 기능 설계 시 이 목록을 먼저 확인.

### 데이터 관련

- **FMP 이력 윈도우는 5년 롤링**
  - 현 구독 플랜이 `historical-price-eod/full` 호출 시 최근 ~1255 거래일만 반환
  - `update_ohlcv` 를 증분 append 방식으로 설계한 이유 — 매일 돌면서 S3 에 쌓이는 데이터는 롤링 창 밖으로도 영구 보존됨
  - 단, **최초 백필 시점 이전의 과거 데이터는 복구 불가능**. MVP 첫 백필(2026-04-24) 기준 약 2021-04 이후만 보유

- **증분 업데이트는 과거 행을 갱신하지 않음**
  - FMP 가 분할·배당으로 과거 `adjClose` 를 소급 수정해도 이미 저장된 행은 그대로 유지됨
  - 엄격한 시계열 정합이 필요한 백테스트 단계에서는 주기적 재백필(`fetch_and_store_ohlcv` 사용)로 보완 필요
  - MVP 의 Bull/Bear 에이전트는 추세·수준 판단이 주 용도라 이 편차는 허용 범위

- **생존 편향 (Survivorship Bias)**
  - `update_ohlcv` 는 **현재** 구성종목만 갱신. 편출된 종목의 OHLCV 는 더 이상 업데이트되지 않음
  - 긴 기간의 백테스트 시 생존 편향 주의. 편출 이력은 `metadata/constituents_changes.parquet` 에 남아있어 명시적 재현 가능

- **Wikipedia 스크래핑 의존**
  - S&P 500 구성종목 소스가 Wikipedia HTML 구조에 의존. 테이블 구조 변경 시 `sp500_wikipedia.py` 업데이트 필요
  - 정상 실행 기준: `fetched` 로그의 `current_members` 가 500~505 범위

- **Dual-class 티커 표기 혼선**
  - Wikipedia/일반 표기: `BRK.B`, `BF.B` (점)
  - FMP 표기: `BRK-B`, `BF-B` (하이픈)
  - FMP 클라이언트 레벨에서 자동 변환 중 ([fmp_client.py](src/common/fmp_client.py)). 다른 데이터 소스 추가 시 같은 규약 적용 확인 필요

### 인프라·비용 관련

- **Lambda zip 크기 한계 (50MB 직접 업로드)**
  - 현재 zip 크기 ~50MB 근접 (pyarrow + pydantic + lxml + bs4)
  - 추가 의존성을 도입하려면 먼저 가능한 제거(boto3 는 런타임 기본) 또는 Layer 분리 검토
  - S3 경유 업로드로 전환 시 zipped 250MB 까지 가능

- **Lambda 실행 시간 예산**
  - `update_ohlcv`: 503 심볼 처리에 ~6~8분. 15분 타임아웃 여유 있지만 FMP 지연·재시도 누적 시 빠듯
  - 백필·재적재 같은 대량 작업은 로컬 스크립트로 분리 유지

- **LLM 비용 상한 월 $200** ([CHARTER §2.2](CHARTER.md))
  - LLM 호출 추가 시 커밋 메시지에 예상 월 비용 영향 기재 필수 (CLAUDE.md 규칙)
  - Sonnet 4.6 기본, Opus 는 이유 없이 금지

### 운영 관련

- **주말 실행 불필요**
  - `update_ohlcv` cron 은 `MON-FRI` 로 제한. 주말 실행은 FMP 에 직전 금요일 데이터만 반복 반환 → 호출 낭비
  - 미국 공휴일(추수감사절, 크리스마스 등)은 cron 으로 거를 수 없음. no-op 로 허용 (새 데이터가 없으면 자동으로 `n_updated=0`)

- **구성종목 업데이트와 OHLCV 업데이트의 순서 의존**
  - `update_ohlcv` 는 `current.parquet` 을 읽어 심볼 목록을 만듦
  - 신규 편입 종목은 주간 `update_constituents` 실행 시점에 최초 OHLCV 적재까지 수행 ([handler.py 의 ohlcv_fetched 경로](src/lambdas/update_constituents/handler.py))
  - 즉 편입 → 다음 평일 `update_ohlcv` 실행 → 이후 정상 증분. 이 흐름이 깨지면 신규 종목 데이터 공백 발생 가능

- **AWS 자격증명 범위**
  - GitHub Actions 배포 role: Lambda 코드 업데이트 + Step Functions 정의 업데이트 권한 (`lambda:UpdateFunctionCode`, `states:CreateStateMachine`, `states:UpdateStateMachine`, `iam:PassRole` 제한 condition)
  - **자동화된 영역**: Lambda 코드 + Step Functions 정의 (M1 부터, M2에서 `agents/` 패키징 추가)
  - **수동 영역 (1회)**: Lambda 함수 생성·환경변수·EventBridge 규칙·IAM 역할 — [`infra/README.md`](infra/README.md) 의 명령으로 복붙 진행
  - 새 Lambda 추가 시: 콘솔 생성 → 환경변수·IAM role 설정 → GitHub Actions 가 자동 배포

---

## 배포 / 실행

### Lambda 자동 배포

`main` 브랜치에 푸시하면 GitHub Actions 가 변경된 Lambda 만 자동 배포합니다.

```bash
# 로컬에서 작업
git add src/lambdas/update_constituents/handler.py
git commit -m "fix(lambda): ..."
git push origin main

# → Actions 에서 자동으로 해당 Lambda 만 재배포
```

수동 트리거:
- GitHub → Actions → **Deploy Lambdas** → **Run workflow** → 특정 Lambda 이름 지정 또는 빈칸 (전체)

### 필요한 AWS 리소스

수동 생성 (1회) — [`infra/README.md`](infra/README.md) 에 IAM 정책 JSON 모두 박아둠.

| 리소스 | 용도 |
|---|---|
| S3 버킷 | Parquet/JSON 저장소 (`metadata/`, `ohlcv/`, `screening/`, `agents/bullbear/`, `scenarios/`, `expected_returns/`, `scenario_contexts/`) |
| Secrets Manager: FMP API 키 | OHLCV·펀더멘털 수집용 |
| Secrets Manager: Anthropic API 키 | Bull/Bear·시나리오 LLM 호출용 (M2 신규) |
| IAM Role: `portfolio-mvp-run_screening-role` | Lambda 실행 (S3 RW, Secrets read) |
| IAM Role: LLM Lambda 실행 (Bull/Bear·시나리오) | S3 RW + FMP·Anthropic Secret read (M2 신규) |
| IAM Role: `portfolio-mvp-step-functions-role` | SF → Lambda invoke (4개 Lambda — run_screening + bullbear_bull/bear + agent_scenario) |
| IAM Role: `portfolio-mvp-eventbridge-role` | EventBridge → SF startExecution |
| IAM Role: `choon-github-actions-role` | CI/CD 배포 (Lambda·Step Functions 업데이트, OIDC) |
| EventBridge 규칙: `update_constituents` | 매주 월 09:00 ET |
| EventBridge 규칙: `update_ohlcv` | 매 평일 22:00 ET |
| EventBridge 규칙: `portfolio-mvp-weekly-screening` | 매주 월 06:00 ET → Step Functions (M1 + M2 + M3 통합 워크플로우) |
| Step Functions: `portfolio-mvp-screening` | RunScreening + BullBearMap (Bull/Bear Parallel) + ScenarioMap |
| **Lambda: `portfolio-mvp-agent_bullbear_bull`** | **Bull 에이전트 (M2 신규)** |
| **Lambda: `portfolio-mvp-agent_bullbear_bear`** | **Bear 에이전트 (M2 신규)** |
| **Lambda: `portfolio-mvp-agent_scenario`** | **시나리오 에이전트 (M3 신규 — single-stance)** |

### Lambda 환경변수

공통 — 모든 Lambda 가 동일 버킷·시크릿 사용:

| 변수 | 필수 | 예시 |
|---|---|---|
| `S3_BUCKET` | ✅ | `portfolio-mvp-data` |
| `FMP_SECRET_ID` | ✅ | `portfolio-mvp/fmp-api-key` |
| `LOG_LEVEL` | | `INFO` (기본값) |

`update_constituents` / `update_ohlcv` 추가:

| 변수 | 기본값 |
|---|---|
| `CONSTITUENTS_PREFIX` | `metadata/constituents` |
| `OHLCV_PREFIX` | `ohlcv` |
| `EVENTS_KEY` | `metadata/constituents_changes.parquet` |

`run_screening` 추가 (M1):

| 변수 | 기본값 |
|---|---|
| `FUNDAMENTALS_PREFIX` | `metadata/fundamentals/key-metrics-ttm` |
| `SCREENING_PREFIX` | `screening` |
| `CACHE_MAX_AGE_DAYS` | `90` (분기 재무 캐시 TTL) |

`agent_bullbear_bull` / `agent_bullbear_bear` 추가 (M2):

| 변수 | 기본값 / 비고 |
|---|---|
| `ANTHROPIC_SECRET_ID` | ✅ 필수 — Anthropic API 키 secret ID |
| `OHLCV_PREFIX` | `ohlcv` (run_screening과 공유) |
| `AGENTS_PREFIX` | `agents/bullbear` (Bull/Bear 출력 + 입력 context lineage) |
| `INCOME_QUARTERLY_PREFIX` | `metadata/fundamentals/income-statement-quarterly` |
| `CASHFLOW_QUARTERLY_PREFIX` | `metadata/fundamentals/cash-flow-statement-quarterly` |
| `CACHE_MAX_AGE_DAYS` | `90` |

`agent_scenario` 추가 (M3):

| 변수 | 기본값 / 비고 |
|---|---|
| `ANTHROPIC_SECRET_ID` | ✅ 필수 — Anthropic API 키 secret ID |
| `OHLCV_PREFIX` | `ohlcv` (공유) |
| `BULLBEAR_PREFIX` | `agents/bullbear` (입력 Bull/Bear 의견 2개 로드) |
| `SCENARIOS_PREFIX` | `scenarios` (LLM 출력 ScenarioOpinion) |
| `EXPECTED_RETURNS_PREFIX` | `expected_returns` (산식 결과 ExpectedReturn — 4단계 입력) |
| `SCENARIO_CONTEXTS_PREFIX` | `scenario_contexts` (입력 context lineage) |
| `INCOME_QUARTERLY_PREFIX` | `metadata/fundamentals/income-statement-quarterly` (ttm_eps) |
| `CACHE_MAX_AGE_DAYS` | `90` |
| `SCENARIO_*` | pricing config override (선택 — §4.3, 기본값은 `pricing_config.py`) |

---

## 주요 설계 결정

| 결정 | 이유 | 참조 |
|---|---|---|
| S&P 500 유니버스 | 유동성 충분, FMP 데이터 기확보, 실험 반복 속도 ↑ | [CHARTER §3.1](CHARTER.md) |
| 주 1회 리밸런싱 | 거래비용·LLM 비용 과다 방지 | [CHARTER §2.4](CHARTER.md) |
| 기본 모델 Sonnet 4.6 | 비용 대비 성능. Opus 는 이유 없이 금지 | [CLAUDE.md](CLAUDE.md) |
| LLM 은 근거 생성만, 매매는 룰 기반 | 할루시네이션 리스크 차단 | [CHARTER §6](CHARTER.md) |
| 월 LLM 비용 상한 $200 | 하드캡. 초과 시 에이전트 수 축소 → 모델 다운그레이드 | [CHARTER §2.2](CHARTER.md) |
| Wikipedia 를 S&P 500 구성종목 소스로 | FMP 플랜 제약 + 현재/이력 테이블 동일 페이지 제공 | 구현 |
| Lambda 직접 번들 (Layer 미사용) | Layer 는 250MB 합계 제한에 걸리기 쉬움. 번들 관리가 단순 | 구현 |
| FMP `key-metrics-ttm` 단일 엔드포인트 + P/E 도출 | 호출 수 절반 (분기 시즌 ~1,050회 → ~530회), 캐시 키 단순 | [docs/01-screening.md §2.1](docs/01-screening.md) |
| 데이터 레이어 ↔ 워크플로우 분리 (EventBridge 별도 트리거) | OHLCV 갱신 실패해도 어제 캐시로 스크리닝 진행 가능 — CHARTER §4.1 우호 | [docs/01-screening.md §4.1](docs/01-screening.md) |
| Plain ASL JSON + AWS CLI 스크립트 (M1 IaC) | 워크플로우 1 state 에 SAM/CDK 는 과함. M2 복잡도 증가 시 재검토 | [infra/README.md](infra/README.md) |
| peer_context: sub_sector → sector 폴백 | dry-run 에서 singleton sub_sector 가 빈번함을 발견 (NEM Gold, USB Banks-Regional 등) → 모든 selected 가 5 peers 보장 | [docs/01-screening.md §10](docs/01-screening.md) |
| Schema bound 1~50 vs 정책 15~20 | schema 는 데이터 형태 sanity, 정책은 pipeline `target_min/max` 기본값 — dry-run/테스트 유연성 | [docs/01-screening.md §2.3](docs/01-screening.md) |
| **Bull/Bear `StockContext` 평탄화 (M2)** | ScreenedStock 임베드 대신 1-depth 명시 필드 — 스크리닝-Bull/Bear 결합도 ↓, 입력 토큰 효율 ↑, "LLM이 보는 형태"가 한 타입에 박제 | [docs/02-bull-bear.md §2.1](docs/02-bull-bear.md) |
| **결정성 캐시 키 = `context_input_hash` (M2)** | StockContext SHA-256으로 같은 입력 재호출 시 LLM 호출 생략 — 운영 cache hit 100% 검증 | [docs/02-bull-bear.md §10](docs/02-bull-bear.md) |
| **`MaxConcurrency=1` Map (M2)** | Anthropic 8K tok/min 한도 회피 (`MaxConcurrency × 2 stance × max_tokens ≤ 한도`). 1차 dry-run에서 5종목 사다리 3회 실패 후 5→1 변경 | [docs/02-bull-bear.md §5.2.2](docs/02-bull-bear.md) |
| **`max_tokens 2048` (M2)** | 골든 1차 실행에서 AAPL_bear가 1024 hit으로 잘림 → 2048 상향. 출력 평균 ~900, 비용 영향 없음 (실 사용량 청구) | [docs/02-bull-bear.md §5.2.1](docs/02-bull-bear.md) |
| **단일 Sector context 프롬프트 (M2)** | 6 sub-sector 검증 — LLM이 sector-specific 회계 차이를 자동 활용 (Bank EV/EBITDA 회피, REIT FFO 부재 자각, Utility capex 인지). 추가 sector별 분기 불필요 | [docs/02-bull-bear.md §10](docs/02-bull-bear.md) |
| **Bull/Bear thin wrapper + 공유 lambda_core (M2)** | 두 Lambda가 동일 코어 import, stance만 다르게 주입. 코드 중복 0, 모델·프롬프트 격리 | [docs/02-bull-bear.md §4.2](docs/02-bull-bear.md) |
| **시나리오 옵션 C — LLM ≠ 가격 산정 분리 (M3)** | LLM 은 narrative·확률·트리거만, `scenario_prices` 는 결정적 산식. 가격 할루시네이션 차단 + 저장 의견에서 ExpectedReturn 결정적 재현(골든 exact assert) | [docs/03-scenario.md §1](docs/03-scenario.md) |
| **percentile 손수 구현 (numpy 미사용) (M3)** | 사용처가 1곳뿐 → linear interp ~10줄로 numpy 의존 0, 콜드스타트·zip 크기 최소. requirements.txt 무변경 | [docs/03-scenario.md §4.1](docs/03-scenario.md) |
| **`ScenarioMap` MaxConcurrency=2 (M3)** | single-stance × 2048 = 4,096 < 8,000 한도. M2 BullBearMap(=1×2 stance) 과 동일 부하, wall-clock 2x 단축. BullBearMap 종료 후 시작이라 동시 호출 없음 | [docs/03-scenario.md §6.1](docs/03-scenario.md) |
| **`scenario_input_hash` 결정성 캐시 (M3)** | M2 패턴 재사용 — ScenarioContext(−lineage) SHA-256. 재실행 폭주 방지 + 재현성(CHARTER 2순위) | [docs/03-scenario.md §6.2](docs/03-scenario.md) |
| **무효화 트리거 observe-only (M3)** | 트리거 평가는 회고 calibration 전용, 라이브 매매·재분석 피드백 없음 — 매매는 룰 기반 정합 | [docs/03-scenario.md §7](docs/03-scenario.md) |

---

## 참고 문서

- **프로젝트 헌장**: [CHARTER.md](CHARTER.md) — 의사결정 기준
- **개발 규칙**: [CLAUDE.md](CLAUDE.md) — 코딩 컨벤션, 커밋 규칙, 작업 가이드
- **단계별 설계**:
  - [docs/01-screening.md](docs/01-screening.md) ✅ M1 운영
  - [docs/02-bull-bear.md](docs/02-bull-bear.md) v0.8 ✅ M2 운영
  - [docs/03-scenario.md](docs/03-scenario.md) v0.14 ✅ M3 운영 + [docs/03-scenario-retro.md](docs/03-scenario-retro.md) (4주 회고)
  - `docs/04-optimizer.md` / `docs/05-rebalancing.md` (작성 예정)
- **외부**:
  - [FMP Stable API](https://site.financialmodelingprep.com/developer/docs/stable)
  - [Anthropic API](https://docs.anthropic.com/)
  - [AWS Lambda](https://docs.aws.amazon.com/lambda/)

---

## 진행 로그 (마일스톤)

| 시점 | 이벤트 |
|---|---|
| 2026-04-20 | CHARTER v0.1 확정, repo 초기화 |
| 2026-04-24 | `update_constituents` Lambda bootstrap 실행 성공 |
| 2026-04-24 | S&P 500 데이터 소스 Wikipedia 로 전환 |
| 2026-04-24 | 503 종목 5년치 OHLCV 로컬 백필 완료 |
| 2026-04-24 | `update_constituents` EventBridge 주간 스케줄 연결 |
| 2026-04-24 | `update_ohlcv` Lambda 작성 (일일 증분 업데이트) |
| 2026-04-26 | `update_ohlcv` EventBridge 일일 스케줄 연결 — Scheduler 트리거·로그 확인 완료 |
| 2026-04-27 | [docs/01-screening.md](docs/01-screening.md) 설계 확정 — schemas, universe, factors, normalize, score, peer_context, pipeline 7개 모듈 + 단위 테스트 154 케이스 통과 |
| 2026-04-27 | [docs/02-bull-bear.md](docs/02-bull-bear.md) M2 설계 초안 작성 |
| 2026-04-28 | FMP 펀더멘털 엔드포인트 실측 (`key-metrics-ttm` 단일 엔드포인트로 P/E·EV/EBITDA·FCF Yield 모두 도출) |
| 2026-04-28 | `run_screening` Lambda + Step Functions + EventBridge 배포 — 첫 운영 실행 (483 universe → 20 selected, 150초) |
| 2026-04-28 | dry-run 으로 발견한 peer_context singleton sub_sector 이슈 → sector 폴백 추가 적용 → 모든 selected 5 peers 보장 |
| 2026-04-28 | EventBridge 주간 스케줄 활성화 (월 11:00 UTC) — **M1 1단계 인프라 완료** |
| 2026-04-29 | Bull/Bear schemas / mappers / context_builder / 프롬프트 / agent.py 구현 (332 단위 테스트 통과) |
| 2026-04-29 | docs/02-bull-bear.md v0.2~v0.3 — 평탄화 StockContext 확정, FMP 분기 statement fetcher 추가 |
| 2026-04-30 | **골든 케이스 첫 실행** — AAPL/XOM/NVDA/JPM × bull/bear (8 의견, $0.166, 회귀 가드 48 통과). max_tokens 1024→2048 갱신, 추천 어휘 정규식 정밀화 |
| 2026-04-30 | Lambda 핸들러 + S3 캐싱 (lambda_core + thin wrapper) — APA 운영 invoke 검증 (cache miss → hit, $0.039) |
| 2026-04-30 | Step Functions ASL 확장 (`BullBearMap` Map MaxConcurrency=1 + Parallel + Catch 격리) |
| 2026-04-30 | **첫 운영 dry-run** (20종목): 40 invoke, retry 0회, **총 비용 $0.083**. 1차 실행에서 rate limit 발견 → MaxConcurrency 5→1 처방 + Haiku schema 보강 |
| 2026-04-30 | 응답 품질 인간 검토 (5 sub-sector sample) — sector 보강 효과 6 sub-sector 최종 검증 |
| 2026-04-30 | EventBridge 정기 트리거 연동 — **M2 마일스톤 §9 #1~#9 완료** ([docs/02-bull-bear.md v0.7](docs/02-bull-bear.md)) |
| 2026-05-24 | Bull/Bear DeepEval G-Eval baseline PoC — 골든 8건 × 3 criteria 24 판정 전수 통과 ($0.37) |
| 2026-05 | docs/02-bull-bear.md v0.8 — M2 4주 누적 운영 평가 박제 ($2.76/월, 160 invoke 100% 성공, retry 0) |
| 2026-05 | **M3 시나리오 설계 v0.1~v0.14 박제** (`docs/03-scenario.md`) — 옵션 C, §2.4~§12 검토 |
| 2026-05 | **M3 구현 #1~#10 완료** — `agents/scenario/` (schemas/pricing_config/pricing/context_builder/agent/trigger_evaluator/lambda_core) + `agent_scenario` Lambda + ASL `ScenarioMap` (G1 체이닝 교정). 시나리오 테스트 추가 (누적 421) |
| 2026-05 | 시나리오 골든 4종목 실제 호출 검증 (AAPL/JPM/NVDA/XOM, $0.072) |
| 2026-05-31 | **시나리오 첫 수동 운영** (20종목) — narrative 300자 한계로 3종목(CRL/WTW/EIX) 실패 → v0.14 max_length 300→500 완화 후 20/20 ($0.054 추가) |
| 2026-06-01 | **시나리오 첫 자동 스케줄 운영** (Week 1) — 20/20, 재시도 0%, $0.36. 음수 skew·캐시 동작 관찰 |
| 2026-06-08 | 시나리오 운영 Week 2 — 20/20, 재시도 0%, $0.36, flags 0. **종목 turnover 25%** 발견 (캐시 주간 무효 → 풀 비용) |
| — | M3 후반: #12 sensitivity 로깅 / #13 트리거 batch / #14 DeepEval + 4주 회고, 이후 4~5단계 연결 |
