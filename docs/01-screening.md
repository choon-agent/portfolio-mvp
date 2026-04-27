# 01. 스크리닝 설계

> **단계**: 1단계 — 코드 기반 스크리닝 (LLM 미사용)
> **상위 문서**: [`CHARTER.md`](../CHARTER.md), [`CLAUDE.md`](../CLAUDE.md)
> **하위 문서**: [`02-bull-bear.md`](02-bull-bear.md) — 본 단계 출력을 입력으로 받음
> **버전**: v0.1 (2026-04-27 초안)
> **상태**: 설계 단계 (M1 마일스톤 — 미구현)

---

## 0. TL;DR

S&P 500 종목에 팩터 기반 점수(모멘텀·밸류)를 매겨 상위 **15~20개**를 골라내는 순수 코드 단계. LLM은 사용하지 않는다(CHARTER §3.3).

- 모델: 없음
- 호출량: FMP 호출 ~520회/주 (캐시 적중 시 ~50회), Lambda 1회/주
- 예상 비용: FMP 요금제 내 + Lambda **월 < $1**
- 출력: `ScreeningResult` (Pydantic) — 종목 리스트 + 팩터 점수 + Bull/Bear가 쓸 `peer_context` 사전계산

---

## 1. 목적과 범위

### 1.1 무엇을 하는가
1. **입력**: 현재 S&P 500 구성종목 ([`Constituent`](../src/common/models.py)) + 가격·재무 캐시
2. **처리**: 유니버스 필터 → 팩터 계산 → 섹터 중립화 → 점수 결합 → 상위 N 선택
3. **출력**:
   - 상위 N 종목 리스트와 종합 점수
   - 각 종목의 원자 팩터 값(모멘텀·밸류 컴포넌트)
   - 같은 `sub_sector` 상위 5개 멀티플 비교 (`peer_context` — Bull/Bear의 토큰 절감용 사전 조립)

### 1.2 비범위 (이 단계가 하지 않는 것)
- ❌ LLM 호출 (CHARTER §3.3 — 1단계는 코드 전용)
- ❌ 매수/매도 결정 (5단계 룰 기반 리밸런서)
- ❌ 포지션 사이징·섹터 가중 (4단계 최적화)
- ❌ 백테스트 엔진 (별도 퀀트 트랙 — CHARTER §1 3순위)
- ❌ 매크로·뉴스·센티먼트 팩터 (FMP 정형 데이터만)
- ❌ Survivorship bias 보정 (MVP에서는 현재 구성원만 — 편출 이력은 보존만)

### 1.3 CHARTER 정합성 체크
| 원칙 | 본 설계 적용 |
|---|---|
| 1순위 LLM 학습 | 본 단계는 LLM 미사용. 단, 출력 스키마가 2단계 입력에 정합하도록 설계 → 학습 레버리지가 큰 2단계에 컨텍스트를 깔끔하게 공급 |
| 2순위 산출물 | 모든 입력·중간·출력 데이터를 S3에 보존. Athena로 사후 재현 가능 |
| 3순위 퀀트 리서치 | "모멘텀 vs 밸류 비교" (CHARTER §4) 측정 가능하도록 팩터별 점수 분리 보존 |
| 비용 상한 월 $200 | 본 단계 비용 거의 없음 → LLM 단계로 예산 집중 |
| 매매는 룰 기반 | 점수·랭킹은 결정적(deterministic). 동일 입력 → 동일 출력 |

---

## 2. 입출력 스키마

### 2.1 입력

| 출처 | 모듈/경로 | 비고 |
|---|---|---|
| S&P 500 구성종목 | [`common/models.py:Constituent`](../src/common/models.py) | `is_current==True`만 사용 |
| 일별 OHLCV | `s3://{bucket}/ohlcv/ticker={SYM}/data.parquet` (스키마는 [`ohlcv.py:OHLCV_SCHEMA`](../src/common/ohlcv.py)) | adj_close 사용 |
| 분기 재무제표 | FMP `income-statement`, `cash-flow-statement` (캐시 90일) | TTM 계산용 |
| 밸류에이션 비율 | FMP `ratios-ttm`, `key-metrics-ttm` (캐시 90일) | P/E, EV/EBITDA, FCF yield |
| 시총·유동성 | FMP `quote` (캐시 1일) | marketCap, avgVolume |

**원칙**: FMP 직접 호출 금지 — [`common/fmp_client.py`](../src/common/fmp_client.py) 캐싱 계층 경유 (CLAUDE.md 규칙).

### 2.2 출력 (`ScreeningResult`)

```python
class FactorScores(BaseModel):
    momentum_12_1m: float | None       # 12개월 수익률 - 직전 1개월 수익률
    momentum_6m: float | None
    pe_ttm: float | None
    ev_ebitda: float | None
    fcf_yield: float | None
    # 섹터 중립화된 z-score (sub_sector 기준)
    momentum_z: float | None
    value_z: float | None

class PeerComparable(BaseModel):
    symbol: str
    pe_ttm: float | None
    ev_ebitda: float | None
    fcf_yield: float | None

class ScreenedStock(BaseModel):
    symbol: str
    company_name: str | None
    sector: str | None
    sub_sector: str | None
    rank: int                          # 1이 최상위
    composite_score: float             # momentum_z + value_z 가중합
    factors: FactorScores
    peer_context: list[PeerComparable] # 같은 sub_sector 상위 5개 (자기 자신 제외)
    data_quality_flags: list[str]      # 예: "missing_fcf", "negative_earnings"

class ScreeningResult(BaseModel):
    as_of_date: date
    universe_size: int                 # 필터 통과 종목 수
    selected: list[ScreenedStock]      # length 15~20
    factor_weights: dict[str, float]   # {"momentum": 0.5, "value": 0.5}
    run_id: str                        # 재현용 (예: "2026-05-04T00:00:00Z")
```

**핵심 결정**: `peer_context`를 본 단계에서 미리 조립한다. Bull/Bear 단계에서 다시 FMP를 호출하지 않게 해 토큰·비용·지연을 줄이고, 동일 시점 데이터로 일관성을 확보한다.

### 2.3 검증
- `selected` 길이 15~20 강제 (Pydantic `Field(min_length=15, max_length=20)`)
- 모든 종목 `peer_context` 길이 ≤ 5 (sub_sector에 5개 미만이면 그만큼만)
- `composite_score` 내림차순으로 `rank` 부여
- 팩터 결측치는 `None` 허용하되, 종합 점수 산출 시 결측 컴포넌트는 0(중립)으로 대체 — `data_quality_flags`에 명시

---

## 3. 팩터·점수 설계

### 3.1 유니버스 필터 (점수 매기기 전 컷)

다음 기준 모두 충족하는 종목만 점수 단계로 진입:

| 컷 | 기준 | 이유 |
|---|---|---|
| 현재 S&P 500 구성원 | `Constituent.is_current` | CHARTER §3.1 유니버스 |
| 시총 ≥ $2B | FMP quote.marketCap | 유동성 확보 |
| 일평균 거래대금 ≥ $20M (직전 60일) | OHLCV에서 계산 | 슬리피지 방지 |
| 직전 12개월 가격 데이터 ≥ 250영업일 | OHLCV row 수 | 모멘텀 계산 가능성 |
| 직전 1년 내 신규 편입 종목 제외 | `Constituent.date_added` | 데이터 안정성 |

필터 통과량 추정: ~480/500 (S&P 500 대부분이 통과).

### 3.2 팩터 정의 (MVP)

**모멘텀**:
- `momentum_12_1m` = `(price_t-21 / price_t-252) - 1` (산업 표준 형태)
- `momentum_6m` = `(price_t-21 / price_t-126) - 1` (보조)
- 결합: 0.7 × `momentum_12_1m` + 0.3 × `momentum_6m`

**밸류**:
- `pe_ttm`: 양수만 (음수 EPS는 결측 처리)
- `ev_ebitda`: 양수만
- `fcf_yield` = `FCF_TTM / EnterpriseValue`
- 결합: 세 컴포넌트를 각각 z-score 후 단순 평균 (P/E·EV/EBITDA는 부호 반전 — 낮을수록 좋음)

**MVP에서 의도적으로 제외**:
- 퀄리티(ROE, 부채비율) — v2에서 추가 검토
- 저변동성, 사이즈 팩터 — 별도 퀀트 트랙

### 3.3 정규화 — 섹터 중립화

**섹터별 z-score**:
```
z_i = (factor_i - mean(factor in same sub_sector)) / std(factor in same sub_sector)
```

이유:
- 섹터 간 멀티플 차이(테크 vs 유틸리티)가 점수를 지배하지 않도록
- CHARTER §3.2 "단일 섹터 ≤35%" 제약과도 정합 (섹터 편향 사전 억제)

`sub_sector` 표본이 5개 미만이면 상위 `sector` 기준으로 폴백.

### 3.4 종합 점수

```
composite_score = w_m × momentum_z + w_v × value_z
```

기본 가중치: `w_m = w_v = 0.5` (CHARTER §4 "모멘텀 vs 밸류 비교"를 위해 두 팩터를 동등 가중으로 시작). 가중치는 `factor_weights`로 출력 — 백테스트 트랙에서 변형 실험 가능.

### 3.5 상위 선택
- `composite_score` 내림차순 상위 20개 → 그 중 `data_quality_flags`가 비어 있는 종목부터 채워 15~20개 확정
- 동점은 `momentum_12_1m` → `fcf_yield` → `symbol` 알파벳 순으로 결정 (재현성)

### 3.6 모듈 단위와 데이터 흐름

§4.3 모듈 배치의 각 모듈은 동작 단위가 다르다 — 일부는 **종목별(per-stock)**, 일부는 **단면(cross-section, 유니버스 전체)** 단위로 동작한다. 이 구분이 모듈 간 호출 순서를 결정한다.

```
[S&P 500 ~500종목]
        │
        ▼
universe.py        ← 유니버스 단위 (전체 리스트 필터링)
        │ 통과 ~478종목
        ▼
factors.py         ← 종목별 (각 종목마다 모멘텀/밸류 raw값 계산)
        │ 종목별 raw 팩터값
        ▼
normalize.py       ← 단면 단위 (같은 sub_sector 내 z-score)
        │ 종목별 z-score
        ▼
score.py           ← 단면 단위 (결합 점수 → 정렬 → 상위 15~20)
        │ 선정 15~20종목
        ▼
peer_context.py    ← 선정 종목별 (sub_sector 상위 5개 멀티플 조립)
        │
        ▼
pipeline.py        ← 위 단계를 순서대로 호출, ScreeningResult 1개 반환
        │
        ▼
ScreeningResult JSON → S3
        │
        ▼
[다음 단계: Bull/Bear]
선정 15~20 종목 각각에 대해
  ├─ Bull 에이전트 1회 호출
  └─ Bear 에이전트 1회 호출  (종목 간 독립 → Map state 병렬)
```

**핵심 포인트**:
- `normalize.py`(섹터 z-score)와 `score.py`(랭킹)는 **다른 종목과의 비교**가 필요하므로 종목별로 분리 호출 불가. 한 종목의 z-score를 구하려면 같은 sub_sector의 다른 종목 평균·표준편차가, 랭킹은 정의상 전체를 줄 세워야 산출 가능.
- 그래서 전체 흐름은 "**유니버스 → 종목별 → 단면 → 종목별 → 합성**"으로 단위가 바뀐다.

**Bull/Bear와의 대비**: Bull/Bear는 종목 간 비교가 필요 없으므로 종목별·관점별로 완전 독립 호출 가능 — [`docs/02-bull-bear.md` §4.1](02-bull-bear.md#41-호출-패턴)의 Step Functions Map state 병렬화 근거가 여기에서 나온다.

---

## 4. 오케스트레이션

### 4.1 실행 패턴

주 1회, 월요일 프리마켓:

```
EventBridge (Mon 06:00 ET, CHARTER §2.4)
        │
        ▼
[Step Functions]
  ├─ Lambda: refresh_constituents (기존 자산 재사용)
  ├─ Lambda: refresh_ohlcv_incremental (기존 자산 재사용)
  ├─ Lambda: refresh_fundamentals_cache (신규 — ratios-ttm 등)
  └─ Lambda: run_screening
        │
        ▼
   S3: screening/dt={yyyy-mm-dd}/result.json
   S3: screening/dt={yyyy-mm-dd}/factors.parquet (Athena 분석용)
        │
        ▼
   다음 단계: Bull/Bear Map state 입력 (docs/02-bull-bear.md §4.1)
```

### 4.2 구성 요소 매핑 (CHARTER §3.4 기존 자산 재사용)
- **EventBridge**: Bull/Bear와 동일 트리거 체인의 첫 작업
- **Step Functions**: 데이터 갱신 → 스크리닝 → Bull/Bear 의 직렬 흐름. 데이터 갱신은 병렬 Branch
- **Lambda**: 단일 인스턴스로 충분 (S&P 500 한 번 처리는 < 60초 예상)
- **S3 레이아웃**:
  - `s3://{bucket}/screening/dt={yyyy-mm-dd}/result.json` — Bull/Bear 입력
  - `s3://{bucket}/screening/dt={yyyy-mm-dd}/factors.parquet` — 사후 분석
  - `s3://{bucket}/screening/dt={yyyy-mm-dd}/input_snapshot.json` — 재현용
- **Athena**: factors.parquet 외부 테이블 → 팩터 IC, 분포 모니터링

### 4.3 모듈 배치

```
src/screening/
├── __init__.py
├── constituents.py      # (기존) 구성종목 빌드/diff
├── universe.py          # 신규 — 3.1 유니버스 필터
├── factors.py           # 신규 — 3.2 모멘텀/밸류 계산 (순수 함수)
├── normalize.py         # 신규 — 3.3 섹터 z-score
├── score.py             # 신규 — 3.4~3.5 결합·랭킹
├── peer_context.py      # 신규 — 2.2 PeerComparable 조립
├── pipeline.py          # 신규 — 위 단계 합성, ScreeningResult 반환
└── schemas.py           # 신규 — 2.2 Pydantic 모델

src/lambdas/
└── run_screening/
    └── handler.py       # 신규 — pipeline.py 호출 + S3 쓰기
```

**원칙 (CLAUDE.md)**: I/O와 순수 로직 분리. `factors.py`/`normalize.py`/`score.py`는 입력 DataFrame/dict를 받아 결과만 반환 — 네트워크·S3 호출 없음.

---

## 5. 비용

LLM 미사용이므로 비용 계산은 인프라 한정.

| 항목 | 추정 |
|---|---|
| FMP 호출 (주간) | ratios-ttm 500종목 + key-metrics 500종목 (캐시 90일이라 분기 발표 시즌만 갱신) → 평균 주간 ~50회, 분기 시작 주는 ~1,050회 |
| OHLCV 증분 | 종목당 1회 × 500 = 500회/주 (이미 [`update_ohlcv`](../src/lambdas/update_ohlcv/) Lambda로 운영) |
| Lambda 실행 | 주 1회 × 60초 × 1024MB ≈ $0.01/월 |
| S3 저장 | 종목 500 × 팩터 parquet ~수 MB/주 → $0.01/월 미만 |

**합계**: 월 **$1 미만** (FMP 요금제 한도 내). LLM 예산($200)에 영향 없음.

### 5.1 비용 가드레일
- FMP 호출 수가 주간 2,000회 초과 시 CloudWatch 알람 (캐시 미스 의심)
- Lambda 실행 시간 5분 초과 시 알람 (성능 회귀)

---

## 6. 로깅과 관측

### 6.1 실행 로그 (CloudWatch)
```json
{
  "timestamp": "2026-05-04T10:00:00Z",
  "purpose": "screening.run",
  "as_of_date": "2026-05-04",
  "universe_after_filter": 478,
  "selected_count": 18,
  "duration_ms": 42100,
  "fmp_calls": 53,
  "fmp_cache_hits": 947,
  "data_quality_warnings": 12
}
```

### 6.2 Athena 분석 테이블
- `screening_factors` — 종목별 팩터 원값과 z-score (시계열)
- `screening_selected` — 선정된 종목과 점수 (시계열)
- 활용: "지난 4주 모멘텀 IC", "선정 종목의 sub_sector 분포 추이" 등

### 6.3 주간 리포트
스크리닝 종료 직후 자동 생성:
- 선정 종목 변경 (이전 주 대비 added/removed)
- 팩터 분포 요약 (평균·분위수)
- `data_quality_flags` 발생 빈도 상위 종목

---

## 7. 실패·예외 처리

| 케이스 | 처리 |
|---|---|
| FMP 5xx/타임아웃 | [`fmp_client.py`](../src/common/fmp_client.py) 지수 백오프 (이미 구현) |
| 특정 종목 가격 데이터 결측 | 유니버스 필터에서 제외, 로그 기록 |
| 특정 종목 재무 결측 (P/E 등) | 해당 컴포넌트만 결측 처리, 다른 팩터로 점수 산출 가능 시 진행 |
| sub_sector 표본 < 5 | 상위 sector로 폴백, 그래도 부족하면 전체 유니버스 z-score |
| 필터 통과 < 50종목 | 비정상 — 실행 중단 + 알람 (FMP 데이터 이상 의심) |
| Lambda 메모리 초과 | 종목을 청크로 나눠 처리 (현재는 일괄 처리 가정 — 회귀 시 적용) |
| Athena 파티션 등록 실패 | 결과는 S3에 이미 있음 → 다음 실행 시 재등록 |

---

## 8. 테스트 전략 (CLAUDE.md 준수)

### 8.1 단위 테스트
- `factors.py`: 가격 시계열 픽스처 → 모멘텀 계산 (영업일 정렬, 결측 처리)
- `normalize.py`: 임의 분포 → 섹터별 z-score 검증 (mean=0, std=1)
- `score.py`: 가중치·동점 처리 (재현성)
- `peer_context.py`: 자기 자신 제외, 상위 5개 절단
- `universe.py`: 각 컷이 독립적으로 종목을 거르는지

### 8.2 통합 테스트
- `pipeline.py`: 픽스처 데이터(20종목) → `ScreeningResult` 산출, 스키마 검증
- `moto`로 S3 모킹, 가짜 FMP 클라이언트 주입 (Protocol)
- 골든 스냅샷: 동일 입력 → 동일 `selected` (재현성 가드)

### 8.3 데이터 품질 테스트 (CI 별도 마커)
- 실제 1주치 데이터로 실행 → 팩터 분포가 합리적 범위인지 (`pytest -m data_quality`)
- 정기 실행은 아니고 수동 트리거

---

## 9. 구현 순서 (M1 마일스톤)

> CLAUDE.md "현재 단계 M0" → M1 진입. 각 단계 별도 커밋. LLM 호출 없으므로 비용 표기 불필요.

1. **schemas.py** — `FactorScores`, `PeerComparable`, `ScreenedStock`, `ScreeningResult` + 단위 테스트
2. **universe.py** — 3.1 필터 (순수 함수, 픽스처 테스트)
3. **factors.py** — 모멘텀·밸류 컴포넌트 계산 (OHLCV/재무 dict 입력)
4. **normalize.py** — 섹터 z-score, 표본 부족 시 폴백
5. **score.py** — 결합 점수, 랭킹, 동점 처리
6. **peer_context.py** — sub_sector 상위 5개 조립
7. **pipeline.py** — 위 모듈 합성, `ScreeningResult` 반환 (FakeFMPClient 주입식)
8. **Lambda 핸들러** — `src/lambdas/run_screening/handler.py` (S3 쓰기)
9. **EventBridge + Step Functions 연결** — 5종목으로 dry run
10. **첫 주간 실행** — 결과 검토, Bull/Bear 입력으로 핸드오프

---

## 10. 미해결 / 다음 결정 필요

- [ ] **결합 가중치**: `w_m = w_v = 0.5`로 시작 후, M2~M3에서 백테스트 트랙으로 가중치 민감도 평가. 가중치를 환경변수/Step Functions 입력으로 외부화할지 결정.
- [ ] **재무 데이터 lag**: 분기 발표는 회계기간 종료 후 4~8주 지연. 발표 전 시점에 어떤 분기를 TTM에 포함시킬지(announce_date 기준 vs filing_date 기준) — FMP 응답 필드 실측 후 결정.
- [ ] **데이터 결측 종목 정책**: 두 팩터 모두 결측인 종목은 자동 제외. 한쪽만 결측인 종목을 어디까지 허용할지 (현재안: 결측 컴포넌트=0 중립) — 첫 주간 실행 후 실측 분포 보고 조정.
- [ ] **Survivorship bias**: 편출 종목 OHLCV는 보존되지만 본 MVP에서는 활용 안 함. 백테스트 트랙으로 이관 시점.
- [ ] **`peer_context` 범위**: 같은 `sub_sector`로 충분한지, 아니면 `sector`까지 확장할지 — Bull/Bear 골든 케이스 평가 후 결정.
- [ ] **선정 종목 안정성**: 주 단위 turnover가 너무 높으면 거래비용 부담. M1 종료 시 4주 turnover 측정, 필요 시 hysteresis(랭크 버퍼) 도입.

---

## 부록 A. Bull/Bear와의 인터페이스 계약

[`docs/02-bull-bear.md` §2.1](02-bull-bear.md#21-입력-stockcontext) `StockContext` 입력에 대한 본 단계의 공급 책임:

| Bull/Bear가 요구하는 필드 | 본 단계 출력 위치 | 비고 |
|---|---|---|
| `symbol`, `company_name`, `sector`, `sub_sector` | `ScreenedStock` 직접 필드 | `Constituent`에서 그대로 |
| `as_of_date` | `ScreeningResult.as_of_date` | 리밸런싱 기준일 = 스크리닝 실행일 |
| `screening_score` | `ScreenedStock.composite_score` | 참고용 — Bull/Bear는 직접 사용 안 함 |
| `peer_context` | `ScreenedStock.peer_context` | 사전 조립 |
| `price_summary`, `fundamentals`, `valuation` | (Bull/Bear의 `context_builder`가 캐시에서 직접 조립) | 본 단계는 미공급 — 단, 동일 캐시 시점 보장 위해 `run_id` 공유 |

→ Bull/Bear 단계는 본 단계 결과 JSON과 동일 시점의 FMP 캐시를 참조해 `StockContext`를 완성한다.

## 부록 B. 참고

- 모멘텀 12-1m: Jegadeesh & Titman (1993) 표준 형태
- 섹터 중립화: Asness 등의 멀티팩터 표준 전처리
- 다음 문서: [`docs/02-bull-bear.md`](02-bull-bear.md) — 본 단계 출력을 입력으로 받는 Bull/Bear 리서치 단계
