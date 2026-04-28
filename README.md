# portfolio-mvp

> LLM 에이전트 오케스트레이션 학습을 위한 주식 포트폴리오 관리 시스템.
> S&P 500 유니버스, 주 1회 리밸런싱, Bull/Bear 에이전트 기반 종목 리서치.

**기간**: 2026-04-20 ~ 2026-10-20 (6개월 MVP)
**현재 단계**: **M1 — 1단계(스크리닝) 운영 시작, 2단계(Bull/Bear) 진행 예정**

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
[1] 스크리닝          → 코드 기반 팩터 스코어 (LLM 사용 X)
       ↓
[2] Bull/Bear 리서치  → 종목당 LLM 에이전트 2개 (핵심 학습 포인트)
       ↓
[3] 시나리오 모델링   → 상위 15~20개만 대상
       ↓
[4] 포트폴리오 최적화  → PyPortfolioOpt 등 (LLM 사용 X)
       ↓
[5] 리밸런싱          → 룰 기반 매매, LLM 은 근거 생성만
```

유니버스 10~15 종목, 섹터당 ≤35%, 월 LLM 비용 상한 $200.

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

---

## 디렉토리 구조

```
portfolio-mvp/
├── CHARTER.md                       # 프로젝트 헌장 (상위 의사결정 원칙)
├── CLAUDE.md                        # 개발 규칙 (Claude Code 자동 로드)
├── README.md                        # 이 파일
├── requirements.txt                 # Lambda 번들용 외부 의존성
├── requirements-dev.txt             # 로컬 개발용 (pytest, boto3)
├── docs/
│   ├── 01-screening.md              # 1단계 스크리닝 설계 (확정)
│   └── 02-bull-bear.md              # 2단계 Bull/Bear 설계 (M2 작업 대상)
├── infra/
│   ├── README.md                    # 배포 순서, IAM 정책, EventBridge 명령
│   └── step_functions/
│       └── screening_workflow.asl.json  # 스크리닝 워크플로우 정의 (M1)
├── src/
│   ├── common/
│   │   ├── fmp_client.py            # FMP API 클라이언트 (OHLCV·key-metrics-ttm·profile)
│   │   ├── fundamentals.py          # key-metrics-ttm cache-aside (S3, 90일 TTL)
│   │   ├── sp500_wikipedia.py       # S&P 500 구성종목 스크래퍼
│   │   ├── s3_io.py                 # S3/Secrets Manager I/O (parquet + JSON)
│   │   ├── ohlcv.py                 # OHLCV 수집·저장
│   │   └── models.py                # Pydantic 도메인 모델 (Constituent 등)
│   ├── screening/                   # 1단계: 코드 기반 스크리닝 (LLM 미사용)
│   │   ├── schemas.py               # FactorScores, ScreenedStock, ScreeningResult
│   │   ├── constituents.py          # 구성종목 diff 로직
│   │   ├── universe.py              # 5개 컷 필터 (시총·유동성·history·seasoning)
│   │   ├── factors.py               # 모멘텀(12-1m, 6m) + 밸류 raw 값 산출
│   │   ├── normalize.py             # sub_sector → sector → universe z-score 폴백
│   │   ├── score.py                 # composite_score, 동점 처리, 상위 15~20 선택
│   │   ├── peer_context.py          # sub_sector → sector peer 폴백
│   │   └── pipeline.py              # 위 모듈 합성, ScreeningResult 반환
│   ├── lambdas/
│   │   ├── update_constituents/handler.py    # 주 1회 구성종목 업데이트
│   │   ├── update_ohlcv/handler.py           # 매 거래일 OHLCV 증분 업데이트
│   │   └── run_screening/handler.py          # 매주 월 스크리닝 실행 (M1 신규)
│   └── tests/                       # pytest 154 케이스 (모든 screening 모듈)
├── scripts/
│   ├── deploy_lambda.sh             # Lambda 패키징·배포
│   ├── deploy_step_functions.sh     # Step Functions 정의 배포 (M1 신규)
│   ├── backfill_ohlcv.py            # OHLCV 초기 백필 (로컬 일회성)
│   ├── dry_run_screening.py         # pipeline 로컬 검증 (FMP 실호출)
│   └── probe_fmp_fundamentals.py    # FMP 엔드포인트 가용성 점검
└── .github/
    └── workflows/
        ├── deploy-lambdas.yml       # CI/CD: Lambda 자동 배포
        └── deploy-step-functions.yml  # CI/CD: Step Functions 자동 배포 (M1 신규)
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

### ✅ 문서화
- CHARTER (헌장): 우선순위·제약·성공 기준 확정
- CLAUDE.md (개발 규칙): 코딩 규칙, 커밋 컨벤션, 비용 상한
- [docs/01-screening.md](docs/01-screening.md): 1단계 설계 (확정, §10 미해결 디자인 채무 명시)
- [docs/02-bull-bear.md](docs/02-bull-bear.md): 2단계 설계 (M2 구현 대기)
- [infra/README.md](infra/README.md): AWS 인프라 배포·운영 가이드

---

## 앞으로 진행할 작업

### M1 (1개월차 마지막까지 목표)

1. **1단계 스크리닝 자동화** ✅ 완료 (위 "현재까지 진행된 작업" 참고)
2. **2단계 Bull/Bear 에이전트 MVP** — CHARTER 의 핵심 학습 포인트, **다음 작업**
   - 설계 [docs/02-bull-bear.md](docs/02-bull-bear.md) 완료 — 구현 대기
   - 종목당 Bull 1 + Bear 1 에이전트 (총 2개)
   - 입력: `screening/dt={date}/result.json` 의 `selected[].peer_context` + Constituent 메타 + OHLCV/펀더멘털 캐시
   - 프롬프트는 `src/agents/prompts/` 에 분리 (`bull_system.md`, `bear_system.md`, `bullbear_user.md`)
   - Pydantic 모델로 출력 검증 (JSON mode)
   - 비용 로깅: `timestamp, model, input_tokens, output_tokens, cost_usd, purpose`
   - 비용 추정: 종목 15~20 × 2 에이전트 × 4회/월 ≈ **월 $5 미만** ([docs/02-bull-bear.md §5.3](docs/02-bull-bear.md))
3. **§10 미해결 디자인 채무 결정** — 4주 운영 데이터 누적 후
   - sector-specific factor 정책 (금융 sector 의 EV/EBITDA·FCF Yield 부적합 — Citi outlier 사례)
   - 선정 종목 turnover 안정성 (4주 데이터로 평가 → 필요 시 hysteresis 도입)
   - 결측 종목 정책 미세조정

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
  - **자동화된 영역**: Lambda 코드 + Step Functions 정의 (M1 부터)
  - **수동 영역 (1회)**: Lambda 함수 생성·환경변수·EventBridge 규칙·IAM 4개 역할 — [`infra/README.md`](infra/README.md) 의 명령으로 복붙 진행
  - 새 Lambda 추가 시: 콘솔 생성 → 환경변수·IAM role 설정 → GitHub Actions 가 자동 배포

### M2 (2개월차)

4. 3~5단계 연결 (시나리오 모델링 → 최적화 → 리밸런싱)
5. 페이퍼 트레이딩 첫 주간 실행 성공

### M3 (3개월차, Phase 1 종료)

6. Charter 재검토 및 실전 전환 판단 (기준은 [CHARTER.md §4.1](CHARTER.md))
7. 블로그 1편 초고

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
| S3 버킷 | Parquet/JSON 저장소 (`metadata/`, `ohlcv/`, `screening/`) |
| Secrets Manager | FMP API 키 (평문 저장) |
| IAM Role: `portfolio-mvp-run_screening-role` | Lambda 실행 (S3 RW, Secrets read) |
| IAM Role: `portfolio-mvp-step-functions-role` | SF → Lambda invoke |
| IAM Role: `portfolio-mvp-eventbridge-role` | EventBridge → SF startExecution |
| IAM Role: `choon-github-actions-role` | CI/CD 배포 (Lambda·Step Functions 업데이트, OIDC) |
| EventBridge 규칙: `update_constituents` | 매주 월 09:00 ET |
| EventBridge 규칙: `update_ohlcv` | 매 평일 22:00 ET |
| EventBridge 규칙: `portfolio-mvp-weekly-screening` | 매주 월 11:00 UTC → Step Functions |
| Step Functions: `portfolio-mvp-screening` | run_screening Lambda 호출 + 재시도 |

### Lambda 환경변수

공통 — 세 Lambda 모두 동일 버킷·시크릿 사용:

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

---

## 참고 문서

- **프로젝트 헌장**: [CHARTER.md](CHARTER.md) — 의사결정 기준
- **개발 규칙**: [CLAUDE.md](CLAUDE.md) — 코딩 컨벤션, 커밋 규칙, 작업 가이드
- **단계별 설계**: `docs/01-screening.md` ~ `docs/05-rebalancing.md` (작성 예정)
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
| — | Bull/Bear 에이전트 MVP (M2 다음 작업) |
