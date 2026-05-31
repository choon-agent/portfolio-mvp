"""시나리오 모델링 단계의 입출력 데이터 스키마.

설계 근거: docs/03-scenario.md §2.1, §2.2, §2.3

이 모듈의 역할:
- 입력 `ScenarioContext` — Bull/Bear 의견 2개 + 가격 컨텍스트. Bull/Bear
  `BullBearOpinion` 을 그대로 임베드 (이미 평탄화 1-depth).
- LLM 출력 `ScenarioOpinion` — 3 시나리오 × (확률 + narrative + 무효화 트리거).
  Pydantic 으로 형식 위반·확률 합·라벨 결측을 모듈 경계에서 차단.
- 산식 출력 `ExpectedReturn` — 결정적 가격 산식(`pricing.py`)이 산출. 사용된
  `ScenarioPricingConfig` 를 함께 보존 (sensitivity·lineage).

핵심 결정 (docs §0, §1.4):
- LLM 은 *가격 숫자를 만들지 않음* — 확률·narrative·측정 가능 트리거만. 가격은
  `pricing.py` 의 결정적 산식.
- `invalidation_trigger` 는 자유 텍스트가 아닌 (metric, direction, threshold)
  3-tuple — 코드 자동 검증 가능 (§7).

LLM 프롬프트 노출 정책 (docs §3.3): identity·가격 컨텍스트·Bull/Bear 의견은
프롬프트로, lineage (run_id/s3_keys/data_quality_flags) 는 미노출 —
이 분리는 schema 가 아니라 context_builder.to_prompt_markdown 가 강제한다.

순수 데이터 스키마 — 네트워크/S3/AWS/LLM 호출 없음.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from agents.bull_bear.schemas import BullBearOpinion
from agents.scenario.pricing_config import ScenarioPricingConfig

__all__ = [
    "ScenarioContext",
    "InvalidationTrigger",
    "Scenario",
    "ScenarioOpinion",
    "ExpectedReturn",
]


# ---------- 입력 ----------


class ScenarioContext(BaseModel):
    """시나리오 에이전트의 단일 호출 입력 (docs §2.1).

    Bull/Bear `BullBearOpinion` 을 그대로 임베드 — 평탄화 안 함 (Bull/Bear
    자체가 이미 1-depth). `current_price`/`ttm_eps`/`peer_pe` 는 LLM 프롬프트와
    코드 산식 *양쪽* 에 사용되는 raw 데이터.

    cross-field 검증을 두지 않은 이유: 결측은 결측 그대로 보존하고 (docs §9),
    호출 가치/fallback 판단은 context_builder·pricing 에 위임 — schema 는 형태만.
    """

    # 1. Identity (from Bull/Bear)
    symbol: str
    company_name: str | None = None
    sector: str | None = None
    sub_sector: str | None = None
    as_of_date: date

    # 2. Bull/Bear 의견 (LLM 노출)
    bull_opinion: BullBearOpinion
    bear_opinion: BullBearOpinion

    # 3. 가격 컨텍스트 (LLM 노출 + 코드 산식 입력)
    current_price: float
    ttm_eps: float | None = None         # None 이면 P/E 기반 가격 산정 불가 (§4.1)
    peer_pe: list[float] = Field(default_factory=list)  # 정렬 무관, 코드가 percentile
    return_52w_high: float | None = None  # (52w_high - current) / current. 양수
    return_52w_low: float | None = None   # (52w_low - current) / current. 음수

    # 4. Lineage (audit/재현 — LLM 프롬프트 미노출)
    run_id: str
    scenario_s3_key: str
    bullbear_s3_keys: dict[Literal["bull", "bear"], str]
    data_quality_flags: list[str] = Field(default_factory=list)


# ---------- LLM 출력 ----------

# 자유 텍스트(자동 검증 불가) qualitative 트리거 — 정량 metric 으로 표현 불가할 때만 (docs §2.4)
_QUALITATIVE_METRICS = frozenset({"peer_announcement", "guidance_change"})


class InvalidationTrigger(BaseModel):
    """시나리오를 무효화하는 *측정 가능* 조건 (docs §2.2).

    자유 텍스트 X — (metric, direction, threshold) 3-tuple 로 §7 자동 검증 가능.
    metric Enum 10개: 자동 측정 8 + 인간 검토 2 (docs §2.4).
    """

    metric: Literal[
        "revenue_yoy", "revenue_qoq", "eps_yoy", "fcf_yoy",
        "gross_margin_yoy", "operating_margin_yoy",
        "earnings_surprise",      # v0.4 — vs 컨센서스, T+0 측정
        "net_debt_yoy",           # v0.4 — BS 기반 레버리지 변화
        "guidance_change", "peer_announcement",
    ]
    direction: Literal["less_than", "greater_than"]
    threshold: float | None = None       # qualitative metric 이면 None
    threshold_unit: Literal["percent", "absolute_usd", "qualitative"]
    description: str = Field(min_length=10, max_length=200)

    @model_validator(mode="after")
    def _validate_metric_unit(self) -> Self:
        """P1-E (docs §2.4) — metric ↔ threshold_unit 정합 강제.

        - qualitative metric (peer_announcement/guidance_change) ⟺ qualitative unit
        - qualitative unit ⟺ threshold is None (정량 metric 은 threshold 필수)
        무효 조합 (revenue_yoy+qualitative, guidance_change+percent 등) 차단.
        """
        is_qual_metric = self.metric in _QUALITATIVE_METRICS
        is_qual_unit = self.threshold_unit == "qualitative"
        if is_qual_metric != is_qual_unit:
            raise ValueError(
                f"metric '{self.metric}' 와 threshold_unit "
                f"'{self.threshold_unit}' 불일치 — qualitative metric 은 "
                f"qualitative unit 이어야 하고 그 역도 성립"
            )
        if is_qual_unit and self.threshold is not None:
            raise ValueError("qualitative trigger 는 threshold 가 None 이어야 함")
        if not is_qual_unit and self.threshold is None:
            raise ValueError(
                f"정량 metric '{self.metric}' 는 threshold (숫자) 가 필요"
            )
        return self


class Scenario(BaseModel):
    """단일 시나리오 (docs §2.2). narrative 는 Bull/Bear evidence 인용."""

    label: Literal["bull", "base", "bear"]
    probability: float = Field(ge=0.0, le=1.0)
    narrative: str = Field(min_length=20, max_length=300)
    invalidation_trigger: InvalidationTrigger


class ScenarioOpinion(BaseModel):
    """시나리오 단일 호출의 검증된 LLM 출력 (docs §2.2).

    3 시나리오 (bull/base/bear 각 1개) + 확률 합 1.0±0.01. 토큰/비용 필드는
    음수만 차단 — 0 허용 (재시도 누적 시 개별 호출 0 기록 가능).
    """

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
        labels = sorted(s.label for s in self.scenarios)
        if labels != ["base", "bear", "bull"]:
            raise ValueError(
                f"라벨 {labels} — bull/base/bear 각 1개씩 필요"
            )
        total = sum(s.probability for s in self.scenarios)
        if not 0.99 <= total <= 1.01:
            raise ValueError(f"확률 합 {total:.3f} — 1.0±0.01 이어야 함")
        return self


# ---------- 산식 출력 ----------


class ExpectedReturn(BaseModel):
    """LLM 출력 + 시장 데이터 + config 로 *결정적 코드* 가 산출 (docs §2.3).

    `pricing_config` 를 함께 보존 — sensitivity 분석·회귀 필수. `data_quality_flags`
    는 가격 순서 위반·결측 fallback 등 산식 단계의 품질 신호 (docs §4.1, §9).
    """

    symbol: str
    as_of_date: date

    # 4단계 최적화의 직접 입력
    expected_price: float
    expected_return: float               # (expected_price - current) / current
    variance: float                      # 확률 가중 분산 — covariance 대각 입력

    # 시나리오별 가격 (감사·디버깅)
    scenario_prices: dict[Literal["bull", "base", "bear"], float]

    # 사용된 파라미터 (sensitivity 분석 필수)
    pricing_config: ScenarioPricingConfig

    # 데이터 품질 플래그 (docs §4.1 v0.3 — 가격 순서 위반·결측 fallback 등)
    data_quality_flags: list[str] = Field(default_factory=list)

    # Lineage
    scenario_opinion_s3_key: str
    computed_at: datetime
