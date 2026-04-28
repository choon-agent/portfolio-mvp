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
| **TTM 펀더멘털** | FMP `key-metrics-ttm` (캐시 90일) | 단일 엔드포인트가 P/E(`1/earningsYieldTTM`), `evToEBITDATTM`, `freeCashFlowYieldTTM`, `marketCap` 제공 — 실측 검증 (`scripts/probe_fmp_fundamentals.py`) |
| 시총 (선택) | FMP `quote` (캐시 1일) | `key-metrics-ttm.marketCap` 으로 대체 가능 — 호출 측 결정 |

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

**Schema 레벨** (Pydantic, 데이터 형태 sanity):
- `selected` 길이 1~50 (`Field(min_length=1, max_length=50)`) — 향후 Russell 확장·dry-run·테스트 유연성
- 모든 종목 `peer_context` 길이 ≤ 5 (sub_sector에 5개 미만이면 그만큼만)
- `composite_score` 내림차순 + `rank` 가 1..N 연속 (model_validator)

**생산 정책 레벨** (pipeline.run_screening 인자):
- `target_min=15`, `target_max=20` 이 기본값 — Lambda 가 그대로 호출하므로 운영 출력은 항상 15~20 범위 (CHARTER §3.2 포지션 수 정책)
- dry-run 이나 단위 테스트는 더 작은 target 으로 호출 가능 (예: 3~5)

**팩터 결측치 처리**:
- `None` 허용하되, 종합 점수 산출 시 결측 z-score 는 0(중립)으로 대체 — `data_quality_flags`에 명시

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

**밸류** (모두 FMP `key-metrics-ttm` 단일 엔드포인트에서 도출):
- `pe_ttm` = `1 / earningsYieldTTM` (양수 yield 일 때만 — 음수면 None)
- `ev_ebitda` = `evToEBITDATTM` (양수만)
- `fcf_yield` = `freeCashFlowYieldTTM` (음수도 보존 — 정당한 부정 시그널)
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

**`schemas.py`가 흐름도에 없는 이유**:
`schemas.py`는 데이터 흐름의 **단계**가 아니라, 흐름 전반에서 공유되는 **타입 계약(type contract)** 이다. 위 다이어그램의 화살표가 schemas가 정의한 "그릇"이고, 각 모듈은 그 그릇에 데이터를 채우거나 다음 그릇으로 변환하는 작업자다.

```
                  schemas.py
        (FactorScores, PeerComparable,
         ScreenedStock, ScreeningResult)
                       ▲
                       │ 모든 모듈이 import
                       │
   universe.py → factors.py → normalize.py → score.py → peer_context.py → pipeline.py
                       │
                       ▼
                 같은 타입을 입출력으로 주고받음
```

각 모듈의 입출력 타입 매핑:
- `universe.py`: `list[Constituent]` → `list[Constituent]` (이미 [`common/models.py`](../src/common/models.py)에 있는 타입 재사용)
- `factors.py`: 종목 데이터 → `FactorScores`
- `normalize.py`: `dict[symbol, FactorScores]` → z-score가 채워진 `FactorScores`
- `score.py`: 위 결과 → `list[ScreenedStock]` (`rank`, `composite_score` 채움)
- `peer_context.py`: 종목 + 유니버스 → `list[PeerComparable]`로 `ScreenedStock` 완성
- `pipeline.py`: 최종 묶음 → `ScreeningResult` 1개

**부가 효과**: Pydantic이 각 단계의 출력을 자동 검증 → 모듈 경계에서 데이터 오염 차단.

**핵심 포인트**:
- `normalize.py`(섹터 z-score)와 `score.py`(랭킹)는 **다른 종목과의 비교**가 필요하므로 종목별로 분리 호출 불가. 한 종목의 z-score를 구하려면 같은 sub_sector의 다른 종목 평균·표준편차가, 랭킹은 정의상 전체를 줄 세워야 산출 가능.
- 그래서 전체 흐름은 "**유니버스 → 종목별 → 단면 → 종목별 → 합성**"으로 단위가 바뀐다.

**Bull/Bear와의 대비**: Bull/Bear는 종목 간 비교가 필요 없으므로 종목별·관점별로 완전 독립 호출 가능 — [`docs/02-bull-bear.md` §4.1](02-bull-bear.md#41-호출-패턴)의 Step Functions Map state 병렬화 근거가 여기에서 나온다.

---

## 4. 오케스트레이션

### 4.1 실행 패턴 — 데이터 레이어와 워크플로우 레이어 분리

주 1회, 월요일 프리마켓. **데이터 갱신은 독립 EventBridge 스케줄로 분리**, Step Functions
는 스크리닝 이후 워크플로우만 담당.

```
[데이터 레이어 — 독립 EventBridge 스케줄, 기존 자산 그대로]
  ├─ update_constituents      (예: 토요일, 주 1회)
  ├─ update_ohlcv_incremental (매일 또는 주말)
  └─ refresh_fundamentals     (분기 시작 주 — 신규, M1 후반)
       │
       ▼
   S3 캐시
       │
       ▼
[스크리닝 워크플로우 — 단일 EventBridge → Step Functions]

EventBridge (Mon 06:00 ET, CHARTER §2.4)
        │
        ▼
[Step Functions]
  └─ Lambda: run_screening
        │
        ▼
   S3: screening/dt={yyyy-mm-dd}/result.json
   S3: screening/dt={yyyy-mm-dd}/factors.parquet (Athena 분석용)
        │
        ▼
   다음 state: Bull/Bear Map → 시나리오 → 최적화 → 리밸런서
   (docs/02-bull-bear.md §4.1)
```

**왜 분리인가**:
- **결합도 ↓ — 견고성 ↑**: OHLCV 갱신이 실패해도 어제 캐시로 스크리닝 진행 가능. CHARTER §4.1
  실전 전환 기준 "주간 리밸런싱 성공률 ≥90%" 달성에 유리.
- **기존 자산 재사용**: `update_constituents`, `update_ohlcv` Lambda 가 이미 EventBridge 스케줄로
  운영 중 — 새 작업 없음.
- **갱신 주기 최적화**: OHLCV 매일, 구성종목 주 1회, 펀더멘털 분기 — 각자 적합한 주기 유지.
- **재실행/디버깅 단순화**: 스크리닝 워크플로우만 단독 재실행 가능 (데이터 갱신 재호출 불필요).
- 주 1회 배치라 데이터 lag(최대 ~12시간) 무의미.

**트레이드오프**: 스크리닝 시점에 갱신 실패가 누적되면 데이터가 낡을 수 있음 → 6.1 로깅으로
캐시 스냅샷 시점을 기록해 사후 추적.

### 4.2 구성 요소 매핑 (CHARTER §3.4 기존 자산 재사용)
- **EventBridge — 데이터 레이어**: 각 update Lambda 별 독립 스케줄
- **EventBridge — 워크플로우 레이어**: Mon 06:00 ET, Step Functions 트리거 (단일)
- **Step Functions**: 스크리닝 → Bull/Bear → 시나리오 → 최적화 → 리밸런서 (직렬 + Map 병렬)
- **Lambda (run_screening)**: 단일 인스턴스로 충분 (S&P 500 한 번 처리는 < 60초 예상)
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
8. **Lambda 핸들러** — `src/lambdas/run_screening/handler.py`. S3 캐시 읽기(constituents, OHLCV) + FMP 호출(ratios-ttm, key-metrics-ttm) + `pipeline.run_screening` + S3 쓰기 (result.json, factors.parquet)
9. **EventBridge + Step Functions 연결** — Mon 06:00 ET 단일 트리거 → Step Functions → run_screening (다음 단계 Bull/Bear Map 은 02-bull-bear.md). 5종목으로 dry run
10. **첫 주간 실행** — 결과 검토, Bull/Bear 입력으로 핸드오프

---

## 10. 미해결 / 다음 결정 필요

- [ ] **결합 가중치**: `w_m = w_v = 0.5`로 시작 후, M2~M3에서 백테스트 트랙으로 가중치 민감도 평가. 가중치를 환경변수/Step Functions 입력으로 외부화할지 결정.
- [x] ~~**FMP 응답 필드 실측**~~: 2026-04 검증 완료 — `key-metrics-ttm` 단일 엔드포인트로 모든 밸류 컴포넌트 + `marketCap` 도출 가능 (P/E TTM 은 `1/earningsYieldTTM`). [`scripts/probe_fmp_fundamentals.py`](../scripts/probe_fmp_fundamentals.py) 로 재현 가능.
- [ ] **재무 데이터 lag**: 분기 발표는 회계기간 종료 후 4~8주 지연. `key-metrics-ttm` 의 TTM 산출 시점(announce_date 기준 vs filing_date 기준) 이 실측 어떻게 동작하는지 — 분기 발표 시즌에 캐시 무효화 정책 결정 필요.
- [ ] **데이터 결측 종목 정책**: 두 팩터 모두 결측인 종목은 자동 제외. 한쪽만 결측인 종목을 어디까지 허용할지 (현재안: 결측 컴포넌트=0 중립) — 첫 주간 실행 후 실측 분포 보고 조정.
- [ ] **Survivorship bias**: 편출 종목 OHLCV는 보존되지만 본 MVP에서는 활용 안 함. 백테스트 트랙으로 이관 시점.
- [x] ~~**`peer_context` 범위**~~: 2026-04-28 30종목 dry-run 에서 singleton sub_sector(MPC, USB)가 빈 peer_context 를 만드는 사례 발견 → **sector 폴백 도입**. 동작: 같은 sub_sector 우선 → 부족하면 같은 sector 의 다른 sub_sector 에서 보충 → 모두 없으면 빈 리스트. ([`peer_context.py`](../src/screening/peer_context.py) `attach_peer_context` 의 폴백 체인 docstring 참고).
- [ ] **선정 종목 안정성**: 주 단위 turnover가 너무 높으면 거래비용 부담. M1 종료 시 4주 turnover 측정, 필요 시 hysteresis(랭크 버퍼) 도입.
- [ ] **Sector-specific 팩터 정책** (M1 dry-run, 2026-04-28 발견): 은행/보험/REIT 등 **금융 sector 는 EV/EBITDA·FCF Yield 가 본질적으로 부적절**. 이유:
  - 예금(은행)·보험준비금(보험)이 EV 정의의 부채에 들어가면서 EV 가 비대해짐 → EV/EBITDA 가 sector 평균 30+ 로 튐
  - 대출 자산 증가가 CF 차감으로 잡혀 영업 호조에도 FCF 음수 흔함 (예: Citigroup TTM FCF -$362B 가 실제 FMP 응답값)
  - 산업 표준은 P/B·ROE·NIM (은행), embedded value (보험) 등
  - **현 영향**: dry-run 결과에서 단일 sub_sector(Diversified Banks 5종목) 안 z-score 가 한 종목으로 크게 왜곡. 단 momentum 차원과 균형 + Bull/Bear 가 컨텍스트로 보강하므로 시스템 동작 자체는 유지.
  - **M2~M3 조치 후보**: (a) 금융 sector 에서 value 차원 가중치 ↓, (b) sector 별 팩터 세트 분기, (c) outlier z-score winsorization (예: ±3σ 클립). 백테스트 트랙으로 평가 후 결정.

---

## 부록 A. Bull/Bear와의 인터페이스 계약

[`docs/02-bull-bear.md` §2.1](02-bull-bear.md#21-입력-stockcontext) `StockContext` 입력에 대한 본 단계의 공급 책임:

| Bull/Bear가 요구하는 필드 | 본 단계 출력 위치 | 비고 |
|---|---|---|
| `symbol`, `company_name`, `sector`, `sub_sector` | `ScreenedStock` 직접 필드 | `Constituent`에서 그대로 |
| `as_of_date` | `ScreeningResult.as_of_date` | 리밸런싱 기준일 = 스크리닝 실행일 |
| `screening_score` | `ScreenedStock.composite_score` | 참고용 — Bull/Bear는 직접 사용 안 함 |
| `peer_context` | `ScreenedStock.peer_context` | 사전 조립 — sub_sector 우선 → 부족하면 sector 폴백 |
| `price_summary`, `fundamentals`, `valuation` | (Bull/Bear의 `context_builder`가 캐시에서 직접 조립) | 본 단계는 미공급 — 단, 동일 캐시 시점 보장 위해 `run_id` 공유 |

→ Bull/Bear 단계는 본 단계 결과 JSON과 동일 시점의 FMP 캐시를 참조해 `StockContext`를 완성한다.

## 부록 B. 참고

- 모멘텀 12-1m: Jegadeesh & Titman (1993) 표준 형태
- 섹터 중립화: Asness 등의 멀티팩터 표준 전처리
- 다음 문서: [`docs/02-bull-bear.md`](02-bull-bear.md) — 본 단계 출력을 입력으로 받는 Bull/Bear 리서치 단계
