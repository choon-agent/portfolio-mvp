# 03. 시나리오 모델링 설계

> **단계**: 3단계 — 시나리오 모델링 (LLM 사용 — 옵션 C: narrative + 확률 + 트리거)
> **상위 문서**: [`CHARTER.md`](../CHARTER.md), [`CLAUDE.md`](../CLAUDE.md)
> **선행 문서**: [`docs/02-bull-bear.md`](02-bull-bear.md) — 본 단계 입력원, M2 운영 중
> **후행 문서**: `docs/04-optimizer.md` — 본 단계 출력(`ExpectedReturn`)을 입력으로 받음
> **버전**: v0.1 (2026-05-25 초안)
> **상태**: 설계 단계 (M3 마일스톤 — 미구현)

---

## 0. TL;DR

Bull/Bear 의견을 입력으로 받아 *3 시나리오 (bull/base/bear) × 확률 + 무효화 트리거*를 LLM 으로 생성, 코드 측 결정적 산식이 가격 범위 + expected return + variance 산출. 4단계 포트폴리오 최적화의 입력.

- 모델: **Sonnet 4.6** (Haiku 4.5 폴백 — 02-bull-bear §4.2 동일 사다리)
- 호출량: 종목당 1회 × 주 15~20 종목 = **주 15~20회 / 월 ~60~80회**
- 예상 비용: **월 ~$1.2~1.6** (CHARTER §3.3 시나리오 단계 추정과 정합)
- 출력: Pydantic 검증 JSON (`ScenarioOpinion`) + 결정적 산식으로 변환된 `ExpectedReturn`
- 핵심 결정: **LLM 은 가격 숫자를 만들지 않음** — 확률 + narrative + 측정 가능 트리거만 생성. 가격 범위는 *historical · peer 데이터 기반 결정적 산식*

---

## 1. 목적과 범위

### 1.1 무엇을 하는가
1. **입력**: 종목 1개의 Bull/Bear 의견 (2 × `BullBearOpinion`) + 가격 컨텍스트 (현재가, TTM EPS, peer P/E, OHLCV 52w high/low)
2. **LLM 처리**: 3개 시나리오 생성 — 각 시나리오는 `(label, probability, narrative, invalidation_trigger)`. 확률 합 = 1.0
3. **코드 처리**: ScenarioPricingConfig + 시장 데이터 → 각 시나리오 가격 → expected return + variance
4. **출력**: `ExpectedReturn` (4단계 최적화의 입력) + `ScenarioOpinion` (원본 LLM 출력, 트리거 자동 검증용)

### 1.2 비범위 (이 단계가 하지 않는 것)
- ❌ **LLM 이 가격 숫자 추정** — Bull/Bear 케이스 가격은 코드 산식이 결정 (옵션 A 거부 근거 §1.4)
- ❌ **포지션 사이징·섹터 가중** (4단계 최적화)
- ❌ **매수/매도 결정** (5단계 룰 기반 리밸런서)
- ❌ **옵션·선물·레버리지·공매도** (CHARTER §5 — bear scenario 도 매수 안 함, 위험 측정용)
- ❌ **백테스트** (별도 퀀트 트랙)
- ❌ **시계열 시뮬레이션** (확률 가중 단일 시점 expected return 만, monte carlo X — v2 후보)

### 1.3 CHARTER 정합성 체크
| 원칙 | 본 설계 적용 |
|---|---|
| 1순위 LLM 학습 | 옵션 C 자체가 *Self-Verification 패턴* (트리거로 자기 시나리오 검증 가능) 학습 포인트 |
| 2순위 산출물 | LLM 출력(`ScenarioOpinion`) + 산식 결과(`ExpectedReturn`) + 사용된 config 모두 S3 보존 → 사후 재현 |
| 3순위 퀀트 리서치 | 가격 산정 공식·config 가 외부화 → sensitivity 실험 가능 |
| 비용 상한 월 $200 | 본 단계 월 ~$1.2~1.6 — 2단계 ($2.76) + 3단계 합쳐도 hard cap 의 2~3% |
| 매매는 룰 기반 (§6) | **LLM 추론 영역 (시나리오·확률) 과 가격 산정 (결정적 코드) 분리** — 할루시네이션이 매매 가격을 직접 결정하지 않음 |

### 1.4 옵션 비교 — 왜 옵션 C 인가

설계 시점 (2026-05-25) 에 3 가지 옵션을 검토:

| 옵션 | LLM 산출 | 할루시네이션 리스크 | 사후 검증 | 4단계 인터페이스 | 결정 |
|---|---|---|---|---|---|
| **A** 가격 타깃 | "12M target = $250" 직접 추정 | 높음 (숫자 자체) | 어려움 | 매끄러움 | ✗ 거부 |
| **B** 코드 점수화만 (LLM 호출 X) | (없음) | 0 | N/A | 단순 | ✗ CHARTER §3.3 LLM 사용 명시와 어긋남 |
| **C** narrative + 확률 + 트리거 | "Bull 시나리오 40%, 트리거: Q2 revenue < $35B" | 낮음 (숫자 X, 검증 가능 트리거 O) | **쉬움** (트리거 객관 측정) | 변환 1단계 (산식) | ✅ **채택** |

옵션 C의 결정적 강점: **LLM 추론 영역(시나리오·확률·narrative) ≠ 가격 산정 영역(결정적 산식)** 의 깔끔한 분리. CHARTER §3.3 LLM 사용 충족 + §6 할루시네이션 리스크 보수 + Self-Verification 패턴 학습.

---

## 2. 입출력 스키마

### 2.1 입력 (`ScenarioContext`)

종목 1개에 대해 호출 직전 조립. **FMP 직접 호출 금지** — 캐싱 계층 (`common/fmp_client.py`, `common/fundamentals.py`) 경유.

```python
class ScenarioContext(BaseModel):
    # 1. Identity (from Bull/Bear)
    symbol: str
    company_name: str | None = None
    sector: str | None = None
    sub_sector: str | None = None
    as_of_date: date

    # 2. Bull/Bear 의견 (LLM 노출)
    bull_opinion: BullBearOpinion        # 02-bull-bear §2.2
    bear_opinion: BullBearOpinion

    # 3. 가격 컨텍스트 (LLM 노출 + 코드 산식 입력)
    current_price: float
    ttm_eps: float | None                # 코드 산식 입력. None 이면 P/E 기반 가격 산정 불가 (§4.1 처리)
    peer_pe: list[float]                 # 정렬 무관, 코드가 percentile 계산
    return_52w_high: float | None        # (52w_high - current) / current. 양수
    return_52w_low: float | None         # (52w_low - current) / current. 음수

    # 4. Lineage (audit/재현 — LLM 미노출)
    run_id: str
    scenario_s3_key: str                 # 본 단계 출력 저장 경로
    bullbear_s3_keys: dict[Literal["bull", "bear"], str]  # 입력 의견 원본
    data_quality_flags: list[str] = Field(default_factory=list)
```

**원칙**:
- Bull/Bear `BullBearOpinion` 을 그대로 임베드 — 평탄화 안 함 (Bull/Bear 자체가 이미 평탄화 1-depth)
- `current_price` / `ttm_eps` / `peer_pe` 는 LLM 프롬프트와 코드 산식 *양쪽 모두* 에 사용 — 가격 산정의 raw 데이터
- `peer_pe` 는 정렬되지 않은 리스트로 받음. 코드가 percentile 계산 (config 의 `peer_pe_*_percentile` 적용)

### 2.2 출력 (`ScenarioOpinion`)

LLM 응답을 Pydantic 검증.

```python
class InvalidationTrigger(BaseModel):
    """시나리오를 무효화하는 *측정 가능* 조건. 자유 텍스트 X — enum + threshold 구조."""
    metric: Literal[
        "revenue_yoy", "revenue_qoq", "eps_yoy", "fcf_yoy",
        "gross_margin_yoy", "operating_margin_yoy",
        "guidance_change", "peer_announcement",
    ]
    direction: Literal["less_than", "greater_than"]
    threshold: float | None              # qualitative metric 이면 None
    threshold_unit: Literal["percent", "absolute_usd", "qualitative"]
    description: str = Field(min_length=10, max_length=200)


class Scenario(BaseModel):
    label: Literal["bull", "base", "bear"]
    probability: float = Field(ge=0.0, le=1.0)
    narrative: str = Field(min_length=20, max_length=300)
    invalidation_trigger: InvalidationTrigger


class ScenarioOpinion(BaseModel):
    symbol: str
    as_of_date: date
    scenarios: list[Scenario] = Field(min_length=3, max_length=3)

    # 호출 메타 (CLAUDE.md 로깅 규칙)
    model: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _validate_scenarios(self) -> Self:
        labels = [s.label for s in self.scenarios]
        if sorted(labels) != ["base", "bear", "bull"]:
            raise ValueError(f"라벨 {labels} — bull/base/bear 각 1개씩 필요")
        total = sum(s.probability for s in self.scenarios)
        if not 0.99 <= total <= 1.01:
            raise ValueError(f"확률 합 {total:.3f} — 1.0±0.01 이어야 함")
        return self
```

**핵심 결정**:
- `invalidation_trigger` 가 자유 텍스트가 아닌 **(metric, direction, threshold) 3-tuple** — 코드 자동 검증 가능 (§7)
- `metric` Enum 8개는 *측정 가능한 것* 만 (§2.4 — 자동 측정 가능 6개 + 인간 검토 2개)
- 확률 합 1.0±0.01 강제 (LLM 부동소수점 오차 허용)
- `narrative` 20~300 자 — Bull/Bear summary 200 자보다 약간 큼 (시나리오 설명에 여유)

### 2.3 가격 산정 출력 (`ExpectedReturn`)

LLM 출력 + 시장 데이터 + config 로 *결정적 코드* 가 산출.

```python
class ExpectedReturn(BaseModel):
    symbol: str
    as_of_date: date

    # 4단계 최적화의 직접 입력
    expected_price: float
    expected_return: float               # (expected_price - current_price) / current_price
    variance: float                      # 확률 가중 분산 — covariance matrix 입력

    # 시나리오별 가격 (감사·디버깅)
    scenario_prices: dict[Literal["bull", "base", "bear"], float]

    # 사용된 파라미터 (sensitivity 분석 필수)
    pricing_config: ScenarioPricingConfig

    # Lineage
    scenario_opinion_s3_key: str
    computed_at: datetime
```

### 2.4 Metric Enum — 자동 측정 가능 여부

| metric | 데이터 출처 | 자동 측정 |
|---|---|---|
| `revenue_yoy` | FMP `income-statement-quarterly` 최신 Q / 동기 전년 Q | ✅ |
| `revenue_qoq` | 최신 Q / 직전 Q | ✅ |
| `eps_yoy` | income-statement-quarterly `epsdiluted` | ✅ |
| `fcf_yoy` | `cash-flow-statement-quarterly` `freeCashFlow` | ✅ |
| `gross_margin_yoy` | `grossProfit / revenue` 비교 | ✅ |
| `operating_margin_yoy` | `operatingIncome / revenue` 비교 | ✅ |
| `guidance_change` | FMP earnings transcript / press release 텍스트 | ⚠ semi-auto (텍스트 분석 필요, M3 후반 또는 인간 검토) |
| `peer_announcement` | 외부 뉴스 | ❌ 인간 검토 only |

시스템 프롬프트가 *자동 측정 가능 metric 선호* 를 명시 (§3.2). LLM 이 `peer_announcement` 를 남발하면 사후 검증 자동화 불가.

---

## 3. 프롬프트 설계

### 3.1 파일 배치 (CLAUDE.md 규칙)

```
src/agents/
├── scenario/
│   ├── __init__.py             # namespace package — 빈 파일 가능 (deploy_lambda.sh 자동 생성)
│   ├── agent.py                # 호출 진입점 (Bull/Bear agent.py 와 동일 사다리 패턴)
│   ├── context_builder.py      # ScenarioContext 조립 + to_prompt_markdown
│   ├── lambda_core.py          # Lambda 공유 코어 (캐시 hit/miss 분기)
│   ├── pricing.py              # 결정적 가격 산식 (§4) — LLM 호출 없음, 순수 함수
│   ├── pricing_config.py       # ScenarioPricingConfig + 기본값
│   ├── trigger_evaluator.py    # 트리거 자동 검증 (§7) — 별도 모듈
│   └── schemas.py              # ScenarioContext, ScenarioOpinion, ExpectedReturn 등
└── prompts/
    └── scenario_system.md      # 시스템 프롬프트
    └── scenario_user.md        # 사용자 프롬프트 템플릿 (placeholder 4개)
```

`AnthropicSDKCaller` 는 `agents/bull_bear/anthropic_adapter.py` 를 재사용 (모듈 공유).

### 3.2 시스템 프롬프트 핵심

```
당신은 한 종목에 대한 다중 시나리오 분석가다. Bull 의견과 Bear 의견을
입력으로 받아 3개의 시나리오 (bull / base / bear) 를 생성한다.

## Hard rules

1. **No price estimation**. 가격 숫자를 출력하지 않는다. 시나리오 가격은
   downstream 의 결정적 산식이 계산한다. probability 와 narrative,
   invalidation_trigger 만 생성.
2. **Probabilities sum to 1.0**. 3개 시나리오 확률 합은 1.0 (±0.01 허용).
3. **Triggers must be measurable**. invalidation_trigger.metric 은 정해진
   enum 에서만 선택. 가능하면 자동 측정 가능한 metric (revenue_yoy, eps_yoy,
   margin_yoy 등) 을 선호. peer_announcement 같은 qualitative 트리거는
   다른 측정 가능한 metric 으로 표현 불가능할 때만 사용.
4. **Narratives reference Bull/Bear evidence**. 각 시나리오의 narrative 는
   입력의 Bull 또는 Bear 의견에서 구체 evidence 를 인용해야 한다 (예:
   "Bull #2 의 FCF margin 확장 가설이 사실이면").
5. **JSON only**. 정해진 schema 만 반환.

## Output schema (strict — do not deviate)

```json
{
  "scenarios": [
    {
      "label": "bull" | "base" | "bear",
      "probability": 0.0~1.0,
      "narrative": "string, 20~300 chars, citing Bull/Bear evidence",
      "invalidation_trigger": {
        "metric": "revenue_yoy" | "revenue_qoq" | "eps_yoy" | "fcf_yoy" |
                  "gross_margin_yoy" | "operating_margin_yoy" |
                  "guidance_change" | "peer_announcement",
        "direction": "less_than" | "greater_than",
        "threshold": number | null,
        "threshold_unit": "percent" | "absolute_usd" | "qualitative",
        "description": "string, 10~200 chars"
      }
    },
    { "label": "...", ... },
    { "label": "...", ... }
  ]
}
```

Critical rules:
- `scenarios` length must be exactly 3, labels = bull/base/bear (one each)
- probabilities sum to 1.0 (±0.01 tolerance)
- `metric` must be from the enum — do not invent new ones
- `threshold` is null ONLY when `threshold_unit == "qualitative"`
- prefer auto-measurable metrics over qualitative when possible
```

### 3.3 사용자 프롬프트 (placeholder)

```
{context}

---

Task: based on Bull/Bear opinions and price context above, generate 3
scenarios for **{symbol}** as of **{as_of_date}**.

Reminders:
- Do NOT estimate prices — only probability + narrative + invalidation_trigger.
- Probabilities must sum to 1.0.
- Each scenario's narrative must cite specific evidence from the Bull or Bear opinion.
- Invalidation triggers must use auto-measurable metrics when possible.

Return only the JSON object matching the schema.
```

`{context}` 는 `to_prompt_markdown(ScenarioContext)` 가 생성:
1. Identity (symbol, sector, as_of_date)
2. 가격 컨텍스트 (현재가, TTM EPS, peer P/E percentile 사전 계산값, 52w high/low return)
3. Bull 의견 (summary + arguments + key_risks)
4. Bear 의견 (summary + arguments + key_risks)

Bull/Bear `to_prompt_markdown` 과 유사한 화이트리스트 직렬화 (lineage 필드 미노출).

---

## 4. 가격 산정 — 결정적 코드

### 4.1 시나리오 가격 공식

각 시나리오의 가격은 *historical 데이터 + peer P/E + TTM EPS* 결합. config (§4.2) 가 결합 방식 결정.

#### Bull case price
```python
def compute_bull_price(
    current_price: float,
    return_52w_high: float | None,
    ttm_eps: float | None,
    peer_pe: list[float],
    cfg: ScenarioPricingConfig,
) -> float:
    historical_target = (
        current_price * (1 + return_52w_high)
        if return_52w_high is not None else None
    )
    peer_target = (
        percentile(peer_pe, cfg.peer_pe_bull_percentile) * ttm_eps
        if (ttm_eps and ttm_eps > 0 and peer_pe) else None
    )
    return combine(historical_target, peer_target, mode=cfg.bull_aggressiveness)

def combine(a, b, mode):
    """둘 중 하나만 있으면 그 값. 둘 다 있으면 mode 에 따라 결합.
    둘 다 None 이면 current_price 반환 (fallback)."""
    if a is None and b is None:
        return None
    if a is None: return b
    if b is None: return a
    if mode == "conservative": return min(a, b)
    if mode == "aggressive":   return max(a, b)
    return (a + b) / 2   # balanced
```

#### Base case price
```python
def compute_base_price(
    current_price: float,
    ttm_eps: float | None,
    peer_pe: list[float],
    cfg: ScenarioPricingConfig,
) -> float:
    peer_target = (
        percentile(peer_pe, cfg.peer_pe_base_percentile) * ttm_eps
        if (ttm_eps and ttm_eps > 0 and peer_pe) else None
    )
    if peer_target is None:
        return current_price       # fallback — fair value 산정 불가
    if cfg.base_price_cap_pct is None:
        return peer_target
    cap = current_price * (1 + cfg.base_price_cap_pct)
    return min(peer_target, cap)
```

#### Bear case price
```python
def compute_bear_price(
    current_price: float,
    return_52w_low: float | None,
    ttm_eps: float | None,
    peer_pe: list[float],
    cfg: ScenarioPricingConfig,
) -> float:
    historical_target = (
        current_price * (1 + return_52w_low)
        if return_52w_low is not None else None
    )
    peer_target = (
        percentile(peer_pe, cfg.peer_pe_bear_percentile) * ttm_eps
        if (ttm_eps and ttm_eps > 0 and peer_pe) else None
    )
    return combine(historical_target, peer_target, mode=cfg.bear_conservatism)
```

#### Expected return + variance
```python
def compute_expected_return(
    opinion: ScenarioOpinion,
    ctx: ScenarioContext,
    cfg: ScenarioPricingConfig,
) -> ExpectedReturn:
    prices = {
        "bull": compute_bull_price(ctx.current_price, ctx.return_52w_high, ctx.ttm_eps, ctx.peer_pe, cfg),
        "base": compute_base_price(ctx.current_price, ctx.ttm_eps, ctx.peer_pe, cfg),
        "bear": compute_bear_price(ctx.current_price, ctx.return_52w_low, ctx.ttm_eps, ctx.peer_pe, cfg),
    }
    sc = {s.label: s for s in opinion.scenarios}

    # LLM 확률에 cap 적용 (cfg.bull_probability_cap)
    probs = _apply_probability_cap(sc, cfg)

    expected = sum(probs[lbl] * prices[lbl] for lbl in ("bull", "base", "bear"))
    variance = sum(
        probs[lbl] * (prices[lbl] - expected) ** 2 for lbl in ("bull", "base", "bear")
    )

    return ExpectedReturn(
        symbol=opinion.symbol,
        as_of_date=opinion.as_of_date,
        expected_price=expected,
        expected_return=(expected - ctx.current_price) / ctx.current_price,
        variance=variance,
        scenario_prices=prices,
        pricing_config=cfg,
        scenario_opinion_s3_key=ctx.scenario_s3_key,
        computed_at=datetime.now(timezone.utc),
    )
```

### 4.2 ScenarioPricingConfig — 보수성 파라미터 5개

```python
class ScenarioPricingConfig(BaseModel):
    """가격 산정 공식의 보수성 파라미터. 모든 ExpectedReturn 산출에 사용된
    config 를 함께 저장 — 사후 sensitivity 분석·회귀 가능."""

    # 1. Bull/Bear 가격 산정의 historical vs peer 결합 방식
    bull_aggressiveness: Literal["conservative", "balanced", "aggressive"] = "conservative"
    bear_conservatism: Literal["conservative", "balanced", "aggressive"] = "conservative"

    # 2. Peer P/E percentile 폭
    peer_pe_bull_percentile: float = Field(default=75.0, ge=50.0, le=95.0)
    peer_pe_base_percentile: float = Field(default=50.0, ge=40.0, le=60.0)
    peer_pe_bear_percentile: float = Field(default=25.0, ge=5.0, le=50.0)

    # 3. Historical return window (현재는 52w 고정 — 추후 확장 시 사용)
    historical_window_days: int = Field(default=252, ge=63, le=756)

    # 4. Base price cap (현재가 대비 fair value 상한)
    base_price_cap_pct: float | None = Field(default=0.0)
    # 0.0:  base ≤ 현재가 (보수)
    # None: cap 없음

    # 5. LLM 확률 가중치 자체 보정 (마지막 가드)
    bull_probability_cap: float | None = Field(default=None, ge=0.0, le=1.0)
    # 0.5: LLM bull 0.7 적어도 0.5 로 cap
    # None: LLM 출력 그대로
```

**기본값은 보수적 셋팅** — CHARTER §6 할루시네이션 리스크 보수.

### 4.3 Config 변경 채널

| 방법 | 용도 | 변경 빈도 |
|---|---|---|
| `ScenarioPricingConfig` 기본값 (코드) | M3 운영의 *공식 정책* — git 커밋 필요 | 분기별 회고 후 |
| Lambda 환경변수 (`SCENARIO_BULL_AGGRESSIVENESS` 등) | 운영 시 즉시 override (예: 위기 상황 보수화) | 비상 시 |
| Step Functions 입력 JSON (`pricing_config_override`) | dry-run / 백테스트용 일회성 변경 | 회고 분석 시 |

Lambda 핸들러가 환경변수·입력 JSON → `ScenarioPricingConfig.model_validate` 로 파싱. 기본값 + override 병합.

### 4.4 ExpectedReturnsBundle — Sensitivity 옵션 (M3 5주차 이후 권장)

매주 *기본 config + 대안 1~2개*로 동시 계산해 함께 저장:

```python
class ExpectedReturnsBundle(BaseModel):
    primary: ExpectedReturn                          # 기본 config — 4단계 최적화 입력
    alternatives: dict[str, ExpectedReturn]          # 비교용 (LLM 호출 없음, 비용 0)
```

저장: `s3://{bucket}/expected_returns/dt={...}/symbol={SYM}.json`

→ **추가 LLM 호출 없음**. 같은 ScenarioOpinion + 다른 config 만 적용. 비용 0.
→ 회고 시 "보수적 config 가 expected $X 예측 → 실제 $Y. 공격적 config 였다면 $Z" 분석 가능.

---

## 5. 비용

### 5.1 토큰·비용 추정 (단일 호출)

| 항목 | 추정 |
|---|---|
| 시스템 프롬프트 | ~700 tok (Bull/Bear 보다 schema 섹션 약간 큼) |
| ScenarioContext (Bull/Bear 두 opinion + 가격 컨텍스트) | ~2,500 tok |
| 사용자 프롬프트 지시문 | ~150 tok |
| 입력 합계 | **~3,350 tok** |
| 출력 (JSON: 3 scenarios + triggers) | ~500 tok |

Sonnet 4.6 단가 (2026-04 기준): 입력 $3/1M, 출력 $15/1M
- 호출당 비용: 3,350 × $3/1M + 500 × $15/1M ≈ **$0.018**

### 5.2 월 비용 추정

| 시나리오 | 종목/주 | 호출/주 | 호출/월 | 월 비용 |
|---|---|---|---|---|
| 기본 (15 종목) | 15 | 15 | 60 | ~$1.10 |
| 상한 (20 종목) | 20 | 20 | 80 | ~$1.45 |
| 재시도 +20% 가정 | 20 | 24 | 96 | ~$1.75 |

**합계 (2단계 Bull/Bear + 3단계 시나리오)**:
- Bull/Bear $2.76 + 시나리오 $1.45 = **월 ~$4.20** (hard cap $200 의 2.1%)

CHARTER §3.3 "월 $30~$80" 추정 대비 매우 여유. v2 Debate / Self-Consistency 실험 여력 충분.

### 5.3 max_tokens

`AgentConfig.max_tokens = 2048` (Bull/Bear 와 동일). 출력 추정 ~500 tok 대비 충분 여유.

---

## 6. 오케스트레이션

### 6.1 호출 패턴 — Step Functions 확장

기존 `infra/step_functions/screening_workflow.asl.json` 에 `ScenarioMap` state 추가:

```
EventBridge (Mon 06:00 ET)
        │
        ▼
[Step Functions]
  ├─ RunScreening                            ← M1
  ├─ BullBearMap (MaxConcurrency=1)          ← M2
  │     for each ScreenedStock:
  │       Parallel: Bull / Bear → S3
  ├─ ScenarioMap (MaxConcurrency=1)          ← M3 추가
  │     for each ScreenedStock:
  │       ScenarioAgent Lambda
  │         → fetch BullBearOpinion × 2 from S3
  │         → fetch price context (OHLCV, key-metrics-ttm, peer_pe)
  │         → LLM call → ScenarioOpinion
  │         → compute_expected_return → ExpectedReturn
  │         → S3 write (scenarios/, expected_returns/)
  └─ (다음 state: 4단계 최적화 — M3 후반)
```

**MaxConcurrency=1** — Anthropic 8K tok/min 한도 산정 (02-bull-bear §5.2.2 공식):
- ScenarioMap 동시 = 1 종목 × max_tokens 2048 = **2,048 tok 예약** ≤ 8,000 ✓
- BullBearMap 종료 후 ScenarioMap 시작이라 동시 호출 없음 — 사실상 한도 여유 더 큼

### 6.2 구성 요소 매핑
- **Step Functions**: 기존 워크플로우에 `ScenarioMap` state 1개 추가
- **Lambda**: `agent_scenario` 1개 (Bull/Bear 와 달리 stance 분리 없음 — 단일 함수)
- **S3 레이아웃**:
  - `s3://{bucket}/scenarios/dt={yyyy-mm-dd}/symbol={SYM}.json` — `ScenarioOpinion` (LLM 출력)
  - `s3://{bucket}/expected_returns/dt={yyyy-mm-dd}/symbol={SYM}.json` — `ExpectedReturn` (산식 결과)
  - `s3://{bucket}/expected_returns/dt={...}/symbol={SYM}/context.json` — `ScenarioContext` 원본 (재현용)

### 6.3 IaC
- 기존 `deploy_lambda.sh` 가 `src/agents/scenario/` 자동 포함 (deploy_lambda v2 의 `agents/` 패키징 그대로)
- `screening_workflow.asl.json` 에 `ScenarioMap` state + placeholder `<<SCENARIO_LAMBDA>>` 추가
- `deploy_step_functions.sh` 에 placeholder 1개 (`SCENARIO_LAMBDA`) 추가

---

## 7. 트리거 자동 검증 (옵션 C 의 강점 — Self-Verification)

### 7.1 검증 메커니즘

분기 발표 후 (FMP statement 캐시 갱신 시점) 자동 실행. 별도 Lambda 또는 batch 스크립트.

```python
def evaluate_trigger(
    trigger: InvalidationTrigger,
    symbol: str,
    income_quarterly: list[dict],     # FMP 분기 응답
    cashflow_quarterly: list[dict],
) -> TriggerEvaluation:
    if trigger.metric == "revenue_yoy":
        latest = income_quarterly[0]["revenue"]
        prior_year = income_quarterly[4]["revenue"]   # 4분기 전
        if prior_year and prior_year > 0:
            actual = (latest / prior_year - 1) * 100
            met = (
                (trigger.direction == "less_than" and actual < trigger.threshold) or
                (trigger.direction == "greater_than" and actual > trigger.threshold)
            )
            return TriggerEvaluation(actual=actual, threshold=trigger.threshold, met=met)

    elif trigger.metric in {"peer_announcement", "guidance_change"}:
        return TriggerEvaluation(met=None, requires_human_review=True)

    # ... 다른 metric
```

### 7.2 회고 데이터 누적

매주·매월 자동 누적:
- 시나리오별 트리거 적중률 (분기 발표 후 측정)
- 확률 calibration (LLM 이 bull 60% 라고 한 시나리오가 실제로 60% 빈도로 발생했나)
- 가격 산정 정확도 (expected_price vs 실제 가격)

이 데이터로 §12 미해결 항목 *기본 config 조정* 의 근거 마련.

### 7.3 자동 측정 vs 인간 검토

| metric | 자동 측정 | 회고 시 처리 |
|---|---|---|
| revenue / eps / fcf / margin yoy / qoq | ✅ | 분기 발표 후 자동 평가 → met/not_met 기록 |
| `guidance_change` | ⚠ semi-auto | M3 후반 텍스트 분석 모듈 또는 인간 검토 |
| `peer_announcement` | ❌ | 분기 회고 시 인간 검토. 가능하면 다른 metric 으로 표현 권장 (system prompt 명시) |

---

## 8. 로깅과 관측

CLAUDE.md "모든 LLM 호출은 다음을 로깅" 규칙 준수.

### 8.1 호출 로그 (CloudWatch + S3)
```json
{
  "timestamp": "2026-06-01T11:00:00Z",
  "model": "claude-sonnet-4-6",
  "purpose": "scenario",
  "symbol": "AAPL",
  "input_tokens": 3380,
  "output_tokens": 510,
  "cost_usd": 0.0178,
  "latency_ms": 4900,
  "retry_count": 0,
  "schema_valid": true,
  "scenarios_probabilities": [0.40, 0.45, 0.15],   // bull/base/bear
  "expected_return": 0.082                          // 산식 결과 (8.2%)
}
```

### 8.2 결과 보존
- `scenarios/dt=*/symbol=*.json` — LLM `ScenarioOpinion` 원본
- `expected_returns/dt=*/symbol=*.json` — 산식 `ExpectedReturn` + 사용된 config
- `scenario_contexts/dt=*/symbol=*.json` — 입력 `ScenarioContext` 원본 (재현용)

### 8.3 주간 리포트 (자동)
- 시나리오 확률 분포 (bull 평균·표준편차, base/bear 동일)
- expected_return 분포 (종목별·sector별)
- 트리거 metric 사용 빈도 (revenue_yoy 가 60%, peer_announcement 가 5% 같은 패턴)

---

## 9. 실패·예외 처리

| 케이스 | 처리 |
|---|---|
| Anthropic API 5xx/타임아웃 | Bull/Bear 와 동일 사다리 (Sonnet → primary retry → Haiku) |
| Pydantic 검증 실패 (확률 합 ≠ 1.0, 라벨 누락 등) | 사다리 진행, 모두 실패 시 `ScenarioAgentError` raise |
| Rate limit | MaxConcurrency=1 로 사전 방어 + 429 retry |
| `ttm_eps=None` (EPS 결측) | peer-implied 가격 산정 불가 → historical-only 로 fallback. 로그 warning |
| 빈 `peer_pe` (sub_sector singleton 등) | peer-implied 불가 → historical-only |
| `current_price=0` 또는 음수 | 명백한 데이터 오류 — `ScenarioContextError`, 종목 스킵 |
| Bull 또는 Bear `BullBearOpinion` 누락 (Bull/Bear 단계 실패) | 본 단계 스킵 + 로그. 4단계 최적화에 expected_return 없는 종목으로 전달 |

ASL 의 `BullBearParallel.Catch` 와 동일 패턴으로 `ScenarioMap` 안 `Catch[States.ALL]` → `RecordItemFailure` 추가.

---

## 10. 테스트 전략 (CLAUDE.md 준수)

### 10.1 단위 테스트
- `pricing.py`: 가격 산정 공식 — `combine` 모드 3 가지, percentile, None fallback, base cap, probability cap
- `pricing_config.py`: 기본값, 환경변수 파싱, 입력 JSON override 병합
- `schemas.py`: ScenarioOpinion Pydantic — 확률 합, 라벨 unique, narrative 길이, trigger threshold null 조건
- `trigger_evaluator.py`: 각 metric 별 자동 평가 (픽스처 분기 데이터 입력)

### 10.2 LLM 호출 모킹
- Bull/Bear `FakeAnthropicClient` 재사용. 정상/검증실패/5xx 시나리오

### 10.3 골든 케이스 (수동)
- 4종목 (Bull/Bear 골든 그대로: AAPL/XOM/NVDA/JPM) → 시나리오 호출 → 스냅샷 저장
- `tests/golden/scenario/{symbol}.json` — `ScenarioOpinion` + `ExpectedReturn`
- `pytest -m golden` 마커 (기존 `pytest.ini` 에 추가)
- 비용 ~$0.072 (4 × $0.018)

### 10.4 통합 테스트
- `lambda_core.py`: moto S3 모킹 + FakeAnthropicClient + 캐시 hit/miss 분기 (Bull/Bear lambda_core 패턴)
- 12~15 케이스 예상

### 10.5 응답 품질 (DeepEval — M2 §11.5 baseline 패턴 확장)
- 3 criteria 후보:
  - `scenarios_probabilities_calibrated`: 확률이 evidence 와 정합 (예: Bull 의견이 weak 한데 bull 70% 같은 불일치 차단)
  - `triggers_are_measurable`: invalidation_trigger 가 자동 측정 가능 metric 선호
  - `narratives_cite_bullbear`: narrative 가 Bull/Bear evidence 인용
- M3 5주차 이후 운영 데이터로 baseline 확립

---

## 11. 구현 순서 (M3 마일스톤)

> M2 종료 후 진입. 각 단계 별도 커밋. LLM 호출 추가 커밋은 비용 추정 명시 (CLAUDE.md).

1. **schemas.py** — `ScenarioContext` / `ScenarioOpinion` / `InvalidationTrigger` / `ExpectedReturn` / `ScenarioPricingConfig` + 단위 테스트
2. **pricing.py** — `compute_bull/base/bear_price` + `compute_expected_return` 순수 함수. config 3 mode × percentile 테스트
3. **pricing_config.py** — 기본값, 환경변수 파싱, 입력 JSON override 병합
4. **context_builder.py** — Bull/Bear S3 로드 + 가격 컨텍스트 조립 + `to_prompt_markdown` 화이트리스트 직렬화
5. **프롬프트 파일 2종** — `scenario_system.md` + `scenario_user.md` (placeholder 4개)
6. **agent.py 골격** — Bull/Bear `agent.py` 패턴 재사용. AnthropicCaller / 사다리 / Pydantic 검증 / 로깅
7. **trigger_evaluator.py** — metric 별 자동 평가 함수 + 단위 테스트
8. **golden 케이스 4건** — AAPL/XOM/NVDA/JPM 실제 호출 + 스냅샷
9. **Lambda 핸들러** — `src/lambdas/agent_scenario/handler.py` + `lambda_core.py` 공유 코어
10. **Step Functions ASL 확장** — `ScenarioMap` state 추가 + `Catch` 격리. 5종목 dry-run
11. **20종목 주간 배치 첫 실행** — 비용·실패율 기록 → M3 회고
12. **(M3 후반)** ExpectedReturnsBundle sensitivity 로깅 활성화
13. **(M3 후반)** 트리거 자동 검증 — 분기 발표 후 자동 평가 batch

---

## 12. 미해결 / 다음 결정 필요

- [ ] **`ScenarioPricingConfig` 기본값 조정 시점** — M3 운영 4주 베이스라인 + Sensitivity 로깅 5~8주차 → M3 말 회고에서 결정. 회고 데이터: 보수적 config 의 expected return 분포 vs 실제 가격 분포, 확률 calibration
- [ ] **`guidance_change` 트리거 자동화** — earnings transcript 텍스트 분석 모듈 필요. M3 후반 또는 v2 후보. 미구현 시 인간 검토 fallback
- [ ] **`peer_announcement` 트리거 정책** — system prompt 가 권장 안 함에도 LLM 이 자주 사용한다면 enum 에서 제거 또는 다른 metric 으로 치환 유도. M3 5주차 운영 데이터로 결정
- [ ] **확률 calibration 평가 방법** — LLM 이 bull 60% 라고 한 시나리오가 실제로 60% 빈도로 발생했나. 4주 운영 데이터로는 부족, M3 말 시점에 12주 데이터로 가능
- [ ] **시나리오 sensitivity 의 portfolio 영향** — 같은 LLM 출력에 다른 config 적용 시 4단계 최적화 결과가 얼마나 달라지나. ExpectedReturnsBundle 로 측정 가능
- [ ] **TTM EPS 결측 종목 정책** — peer-implied 가격 산정 불가. historical-only fallback 의 정확도 영향. 첫 운영 시 결측 분포 본 후 결정
- [ ] **`v2` Monte Carlo 시나리오** — 3 scenarios 단일 시점 대신 distribution. 본 단계 단순 옵션 C 가 4주 안정 운영 후 도입 (M3 후반 후보)

---

## 부록 A. 디렉토리 구조 (M3 진입 시점 — 미구현)

```
src/agents/                              ✅ M2 기존
├── bull_bear/                           ✅ M2 완료
├── scenario/                            ⏳ M3 신규
│   ├── schemas.py                       ⏳ §2.1, §2.2, §2.3
│   ├── pricing.py                       ⏳ §4.1 (순수 함수)
│   ├── pricing_config.py                ⏳ §4.2
│   ├── context_builder.py               ⏳ §3.3 to_prompt_markdown 포함
│   ├── agent.py                         ⏳ §3.2 (Bull/Bear agent.py 패턴 재사용)
│   ├── lambda_core.py                   ⏳ §6.2
│   └── trigger_evaluator.py             ⏳ §7
└── prompts/
    ├── scenario_system.md               ⏳ §3.2
    └── scenario_user.md                 ⏳ §3.3

src/lambdas/
└── agent_scenario/                      ⏳ M3 신규
    └── handler.py                       ⏳ thin wrapper

infra/step_functions/
└── screening_workflow.asl.json          ✅ M2 + ⏳ M3 (ScenarioMap state 추가)

scripts/
└── run_scenario_golden.py               ⏳ M3 신규 (4종목 fixture)

tests/
└── golden/scenario/                     ⏳ M3 신규 ({symbol}.json 4개)
```

## 부록 B. 4단계 (최적화) 인터페이스 계약

`docs/04-optimizer.md` (작성 예정) 가 본 단계 출력을 입력으로 받음.

| 4단계 필요 필드 | 본 단계 출력 | 비고 |
|---|---|---|
| `expected_return` (per symbol) | `ExpectedReturn.expected_return` | 직접 사용 |
| `expected_price` (per symbol) | `ExpectedReturn.expected_price` | 직접 사용 |
| `variance` (per symbol) | `ExpectedReturn.variance` | covariance matrix 의 *대각* 입력. 비대각 (종목 간 상관) 은 4단계가 OHLCV 에서 별도 계산 |
| 시나리오별 가격 (선택) | `ExpectedReturn.scenario_prices` | sensitivity 분석 / portfolio stress test |
| 사용된 config (lineage) | `ExpectedReturn.pricing_config` | 4단계도 이를 로깅해 어떤 config 의 산출이 최적화에 사용됐는지 추적 |

4단계는 `s3://{bucket}/expected_returns/dt={...}/symbol=*.json` 을 모두 로드 후 PyPortfolioOpt 등 라이브러리 입력.

## 부록 C. 참고

- 옵션 C 결정 근거: docs §1.4 표
- 가격 산정 공식 유도: docs §4.1
- Bull/Bear 인터페이스: [`docs/02-bull-bear.md` §2.2](02-bull-bear.md#22-출력-bullbearopinion)
- Anthropic JSON mode + Pydantic 검증 패턴: [`docs/02-bull-bear.md` §3](02-bull-bear.md#3-프롬프트-설계)
- 트리거 자동 검증의 학습 가치: Self-Verification 패턴 (CHARTER §4 LLM 학습 항목 "3가지 에이전트 패턴" 중 하나)
