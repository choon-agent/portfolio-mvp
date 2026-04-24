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
│       └── update_constituents/
│           └── handler.py     # 주 1회 S&P 500 업데이트 Lambda
├── scripts/
│   └── deploy_lambda.sh       # Lambda 패키징·배포 스크립트
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

### ✅ 문서화
- CHARTER (헌장): 우선순위·제약·성공 기준 확정
- CLAUDE.md (개발 규칙): 코딩 규칙, 커밋 컨벤션, 비용 상한

---

## 앞으로 진행할 작업

### 다음 단계 (즉시 착수)

1. **S&P 500 전체 OHLCV 백필** (로컬 일회성 작업)
   - `scripts/backfill_ohlcv.py` 작성 — `common/` 모듈 재사용
   - S3 `current.parquet` 에서 503 심볼 읽어 FMP `/stable/historical-price-eod/full` 호출
   - S3 `ohlcv/<symbol>.parquet` 에 저장
   - Lambda 15분 타임아웃 회피 목적으로 로컬 실행
   - FMP `historical-price-eod/full` 엔드포인트는 현 구독 플랜으로 사용 가능 확인 완료

2. **EventBridge 스케줄 연결**
   - `update_constituents` Lambda 를 매주 월요일 프리마켓 시간에 자동 실행
   - 두번째 실행부터는 bootstrap 대신 실제 diff 기반 동작

### M1 (1개월차 마지막까지 목표)

3. **2단계 Bull/Bear 에이전트 MVP** — CHARTER 의 핵심 학습 포인트
   - 종목당 Bull 1 + Bear 1 에이전트 (총 2개)
   - 프롬프트는 `src/agents/prompts/` 에 분리
   - Pydantic 모델로 출력 검증 (JSON mode)
   - 비용 로깅: `timestamp, model, input_tokens, output_tokens, cost_usd, purpose`
   - 대상 종목: 1단계 스크리닝 통과 15~20개

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
| — | EventBridge 스케줄 연결 (예정) |
| — | OHLCV 백필 (예정) |
| — | Bull/Bear 에이전트 MVP (예정) |
