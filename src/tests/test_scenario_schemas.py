"""시나리오 스키마 단위 테스트.

순수 Pydantic 검증만 — 네트워크/S3/AWS/LLM 호출 없음.
설계 근거: docs/03-scenario.md §2.1, §2.2, §2.3
"""
from __future__ import annotations

from datetime import datetime, timezone
from datetime import date as date_cls

import pytest
from pydantic import ValidationError

from agents.bull_bear.schemas import Argument, BullBearOpinion
from agents.scenario.pricing_config import ScenarioPricingConfig
from agents.scenario.schemas import (
    ExpectedReturn,
    ExpectedReturnsBundle,
    InvalidationTrigger,
    Scenario,
    ScenarioContext,
    ScenarioOpinion,
)

AS_OF = date_cls(2026, 5, 4)


# ---------- 헬퍼 ----------


def _opinion(stance: str = "bull") -> BullBearOpinion:
    return BullBearOpinion(
        symbol="AAPL",
        stance=stance,  # type: ignore[arg-type]
        as_of_date=AS_OF,
        summary=f"{stance} thesis",
        arguments=[
            Argument(claim="c1", evidence="e1", confidence="high"),
            Argument(claim="c2", evidence="e2", confidence="medium"),
            Argument(claim="c3", evidence="e3", confidence="low"),
        ],
        key_risks_to_thesis=["risk1"],
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.01,
    )


def _trigger(**overrides: object) -> InvalidationTrigger:
    base: dict[str, object] = {
        "metric": "revenue_yoy",
        "direction": "less_than",
        "threshold": 5.0,
        "threshold_unit": "percent",
        "description": "Q2 revenue growth below 5%",
    }
    base.update(overrides)
    return InvalidationTrigger(**base)  # type: ignore[arg-type]


def _scenario(label: str, prob: float) -> Scenario:
    return Scenario(
        label=label,  # type: ignore[arg-type]
        probability=prob,
        narrative=f"{label} case driven by Bull #1 evidence on margins",
        invalidation_trigger=_trigger(),
    )


def _opinion_out(probs: tuple[float, float, float] = (0.4, 0.45, 0.15)) -> ScenarioOpinion:
    pb, pba, pbe = probs
    return ScenarioOpinion(
        symbol="AAPL",
        as_of_date=AS_OF,
        scenarios=[
            _scenario("bull", pb),
            _scenario("base", pba),
            _scenario("bear", pbe),
        ],
        model="claude-sonnet-4-6",
        input_tokens=3000,
        output_tokens=500,
        cost_usd=0.018,
    )


# ---------- InvalidationTrigger: P1-E metric↔unit validator (§2.4) ----------


def test_trigger_quantitative_valid() -> None:
    t = _trigger(metric="eps_yoy", threshold_unit="percent", threshold=-10.0)
    assert t.threshold == -10.0


def test_trigger_qualitative_valid() -> None:
    t = _trigger(
        metric="peer_announcement",
        threshold_unit="qualitative",
        threshold=None,
    )
    assert t.threshold is None


def test_trigger_v04_metrics_valid() -> None:
    for metric in ("earnings_surprise", "net_debt_yoy"):
        t = _trigger(metric=metric, threshold_unit="percent", threshold=8.0)
        assert t.metric == metric


def test_trigger_quantitative_with_qualitative_unit_rejected() -> None:
    with pytest.raises(ValidationError, match="불일치"):
        _trigger(metric="revenue_yoy", threshold_unit="qualitative", threshold=None)


def test_trigger_qualitative_metric_with_percent_rejected() -> None:
    with pytest.raises(ValidationError, match="불일치"):
        _trigger(metric="guidance_change", threshold_unit="percent", threshold=5.0)


def test_trigger_qualitative_unit_with_threshold_rejected() -> None:
    with pytest.raises(ValidationError, match="threshold 가 None"):
        _trigger(
            metric="peer_announcement",
            threshold_unit="qualitative",
            threshold=1.0,
        )


def test_trigger_quantitative_without_threshold_rejected() -> None:
    with pytest.raises(ValidationError, match="threshold .* 필요"):
        _trigger(metric="revenue_yoy", threshold_unit="percent", threshold=None)


def test_trigger_description_length_enforced() -> None:
    with pytest.raises(ValidationError):
        _trigger(description="short")  # < 10
    with pytest.raises(ValidationError):
        _trigger(description="x" * 201)  # > 200


def test_trigger_unknown_metric_rejected() -> None:
    with pytest.raises(ValidationError):
        _trigger(metric="pe_ratio")


# ---------- Scenario 필드 제약 ----------


def test_scenario_narrative_length() -> None:
    with pytest.raises(ValidationError):
        Scenario(
            label="bull",
            probability=0.4,
            narrative="too short",  # < 20
            invalidation_trigger=_trigger(),
        )


def _narrative(n: int) -> Scenario:
    return Scenario(
        label="bull", probability=0.4, narrative="x" * n,
        invalidation_trigger=_trigger(),
    )


def test_scenario_narrative_max_500() -> None:
    # v0.14 — 300→500 완화 (M3 첫 운영 evidence 인용 narrative 초과 대응)
    assert _narrative(400).narrative == "x" * 400  # 350 타깃 overshoot 흡수
    assert _narrative(500).narrative == "x" * 500  # 경계
    with pytest.raises(ValidationError):
        _narrative(501)  # > 500


@pytest.mark.parametrize("bad", [-0.1, 1.1])
def test_scenario_probability_bounds(bad: float) -> None:
    with pytest.raises(ValidationError):
        _scenario("bull", bad)


# ---------- ScenarioOpinion validator (§2.2) ----------


def test_opinion_valid() -> None:
    op = _opinion_out()
    assert len(op.scenarios) == 3


def test_opinion_probabilities_sum_tolerance() -> None:
    # 합 1.0 ± 0.01 허용
    op = _opinion_out((0.40, 0.45, 0.155))  # 합 1.005
    assert op is not None


def test_opinion_probabilities_sum_rejected() -> None:
    with pytest.raises(ValidationError, match="확률 합"):
        _opinion_out((0.4, 0.4, 0.4))  # 합 1.2


def test_opinion_missing_label_rejected() -> None:
    with pytest.raises(ValidationError, match="bull/base/bear"):
        ScenarioOpinion(
            symbol="AAPL",
            as_of_date=AS_OF,
            scenarios=[
                _scenario("bull", 0.5),
                _scenario("bull", 0.3),  # base 누락, bull 중복
                _scenario("bear", 0.2),
            ],
            model="m",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )


def test_opinion_requires_exactly_three() -> None:
    with pytest.raises(ValidationError):
        ScenarioOpinion(
            symbol="AAPL",
            as_of_date=AS_OF,
            scenarios=[_scenario("bull", 0.5), _scenario("bear", 0.5)],
            model="m",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )


def test_opinion_negative_tokens_rejected() -> None:
    with pytest.raises(ValidationError):
        _opinion_out_with_meta(input_tokens=-1)


def _opinion_out_with_meta(**meta: object) -> ScenarioOpinion:
    base: dict[str, object] = {
        "symbol": "AAPL",
        "as_of_date": AS_OF,
        "scenarios": [_scenario("bull", 0.4), _scenario("base", 0.45), _scenario("bear", 0.15)],
        "model": "m",
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }
    base.update(meta)
    return ScenarioOpinion(**base)  # type: ignore[arg-type]


# ---------- ScenarioContext (§2.1) ----------


def test_context_embeds_bull_bear() -> None:
    ctx = ScenarioContext(
        symbol="AAPL",
        as_of_date=AS_OF,
        bull_opinion=_opinion("bull"),
        bear_opinion=_opinion("bear"),
        current_price=190.0,
        ttm_eps=6.0,
        peer_pe=[25.0, 30.0, 28.0],
        return_52w_high=0.1,
        return_52w_low=-0.2,
        run_id="2026-05-04T00:00:00Z",
        scenario_s3_key="scenarios/dt=2026-05-04/symbol=AAPL.json",
        bullbear_s3_keys={"bull": "k_bull", "bear": "k_bear"},
    )
    assert ctx.bull_opinion.stance == "bull"
    assert ctx.bear_opinion.stance == "bear"
    assert ctx.data_quality_flags == []


def test_context_missing_optional_price_inputs() -> None:
    # ttm_eps=None, peer_pe=[] 도 허용 — fallback 은 pricing 에서 (§9)
    ctx = ScenarioContext(
        symbol="X",
        as_of_date=AS_OF,
        bull_opinion=_opinion("bull"),
        bear_opinion=_opinion("bear"),
        current_price=10.0,
        run_id="r",
        scenario_s3_key="k",
        bullbear_s3_keys={"bull": "kb", "bear": "kr"},
    )
    assert ctx.ttm_eps is None
    assert ctx.peer_pe == []


# ---------- ExpectedReturn (§2.3) ----------


def test_expected_return_embeds_config_and_flags() -> None:
    er = ExpectedReturn(
        symbol="AAPL",
        as_of_date=AS_OF,
        expected_price=200.0,
        expected_return=0.05,
        variance=120.0,
        scenario_prices={"bull": 230.0, "base": 200.0, "bear": 160.0},
        pricing_config=ScenarioPricingConfig(),
        scenario_opinion_s3_key="scenarios/dt=2026-05-04/symbol=AAPL.json",
        computed_at=datetime(2026, 5, 4, tzinfo=timezone.utc),
    )
    assert er.data_quality_flags == []  # 기본값
    assert er.pricing_config.bull_aggressiveness == "conservative"
    assert er.scenario_prices["bull"] == 230.0


def _expected_return(**overrides: object) -> ExpectedReturn:
    base: dict[str, object] = {
        "symbol": "AAPL", "as_of_date": AS_OF, "expected_price": 200.0,
        "expected_return": 0.05, "variance": 120.0,
        "scenario_prices": {"bull": 230.0, "base": 200.0, "bear": 160.0},
        "pricing_config": ScenarioPricingConfig(),
        "scenario_opinion_s3_key": "k", "computed_at": datetime(2026, 5, 4, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return ExpectedReturn(**base)  # type: ignore[arg-type]


def test_expected_returns_bundle() -> None:
    primary = _expected_return()
    bundle = ExpectedReturnsBundle(
        primary=primary,
        alternatives={"balanced": _expected_return(expected_return=0.09)},
    )
    assert bundle.primary.expected_return == 0.05
    assert bundle.alternatives["balanced"].expected_return == 0.09


def test_expected_returns_bundle_alternatives_default_empty() -> None:
    bundle = ExpectedReturnsBundle(primary=_expected_return())
    assert bundle.alternatives == {}
