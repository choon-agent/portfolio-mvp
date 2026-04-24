# portfolio-mvp

> LLM 에이전트 오케스트레이션 학습을 위한 주식 포트폴리오 관리 시스템.
> S&P 500 유니버스, 주 1회 리밸런싱, Bull/Bear 에이전트 기반 종목 리서치.

**기간**: 2026-04-20 ~ 2026-10-20 (6개월 MVP)
**현재 단계**: **M0 — 인프라 스캐폴딩 및 1단계(스크리닝) 구현 중**

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
├── CHARTER.md                 # 프로젝트 헌장 (상위 의사결정 원칙)
├── CLAUDE.md                  # 개발 규칙 (Claude Code 자동 로드)
├── README.md                  # 이 파일
├── requirements.txt           # Lambda 번들용 외부 의존성
├── src/
│   ├── common/                # 공용 유틸
│   │   ├── fmp_client.py      # FMP API 클라이언트 (stable API, OHLCV 전용)
│   │   ├── sp500_wikipedia.py # S&P 500 구성종목 스크래퍼 (BeautifulSoup)
│   │   ├── s3_io.py           # S3/Secrets Manager I/O, parquet 헬퍼
│   │   ├── ohlcv.py           # OHLCV 수집·저장
│   │   └── models.py          # Pydantic 도메인 모델
│   ├── screening/             # 1단계: 코드 기반 스크리닝
│   │   └── constituents.py    # 구성종목 diff 로직
│   └── lambdas/
│       ├── update_constituents/
│       │   └── handler.py     # 주 1회 S&P 500 구성종목 업데이트
│       └── update_ohlcv/
│           └── handler.py     # 매 거래일 OHLCV 증분 업데이트
├── scripts/
│   ├── deploy_lambda.sh       # Lambda 패키징·배포 스크립트
│   └── backfill_ohlcv.py      # OHLCV 초기 백필 (로컬 일회성)
└── .github/
    └── workflows/
        └── deploy-lambdas.yml # CI/CD: 변경 Lambda 자동 배포
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

### ✅ EventBridge 스케줄
- `update_constituents` — 매주 월요일 ET 09:00 (프리마켓). 구성종목 diff 및 변경 이벤트 로깅
- `update_ohlcv` — 매 평일 ET 22:00 (장 마감 후 FMP EOD 반영 완료 시점). *스케줄은 콘솔 수동 설정*

### ✅ 문서화
- CHARTER (헌장): 우선순위·제약·성공 기준 확정
- CLAUDE.md (개발 규칙): 코딩 규칙, 커밋 컨벤션, 비용 상한

---

## 앞으로 진행할 작업

### 다음 단계 (즉시 착수)

1. **`update_ohlcv` Lambda 의 EventBridge 스케줄 연결**
   - 매 평일 ET 22:00 트리거 (cron: `0 22 ? * MON-FRI *`, timezone: `America/New_York`)
   - 콘솔에서 수동 설정 (Scheduler role 재사용 가능)

### M1 (1개월차 마지막까지 목표)

2. **2단계 Bull/Bear 에이전트 MVP** — CHARTER 의 핵심 학습 포인트
   - 종목당 Bull 1 + Bear 1 에이전트 (총 2개)
   - 프롬프트는 `src/agents/prompts/` 에 분리
   - Pydantic 모델로 출력 검증 (JSON mode)
   - 비용 로깅: `timestamp, model, input_tokens, output_tokens, cost_usd, purpose`
   - 대상 종목: 1단계 스크리닝 통과 15~20개

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
  - GitHub Actions 배포 role: `lambda:UpdateFunctionCode` 등 코드 업데이트 권한만 보유
  - Lambda 함수 생성·설정·EventBridge·IAM 은 **콘솔 수동 작업** (IaC 미도입 상태)
  - 새 Lambda 추가 시 동일 패턴: 콘솔 생성 → 환경변수·IAM role 설정 → GitHub Actions 가 자동 배포

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

### 필요한 AWS 리소스 (사전 생성 필요)

| 리소스 | 용도 |
|---|---|
| S3 버킷 | Parquet 저장소 (`metadata/`, `ohlcv/`) |
| Secrets Manager | FMP API 키 (평문 저장) |
| IAM Role (Lambda 실행용) | S3 읽기/쓰기, Secrets Manager read |
| IAM Role (배포용 `choon-github-actions-role`) | `lambda:UpdateFunctionCode` 등 |
| EventBridge 규칙 | 주간 스케줄 (예정) |

### Lambda 환경변수

| 변수 | 필수 | 예시 |
|---|---|---|
| `S3_BUCKET` | ✅ | `portfolio-mvp-data` |
| `FMP_SECRET_ID` | ✅ | `portfolio-mvp/fmp-api-key` |
| `CONSTITUENTS_PREFIX` | | `metadata/constituents` (기본값) |
| `OHLCV_PREFIX` | | `ohlcv` (기본값) |
| `EVENTS_KEY` | | `metadata/constituents_changes.parquet` (기본값) |
| `LOG_LEVEL` | | `INFO` (기본값) |

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
| — | `update_ohlcv` EventBridge 일일 스케줄 연결 (예정) |
| — | Bull/Bear 에이전트 MVP (예정) |
