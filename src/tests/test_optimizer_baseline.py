"""optimizer.baseline 단위 테스트 (04 §6 — 옵션 B 확률 매핑)."""
from __future__ import annotations

from datetime import date

import pytest

from agents.bull_bear.schemas import Argument, BullBearOpinion
from agents.scenario.pricing_config import ScenarioPricingConfig
from agents.scenario.schemas import (
    InvalidationTrigger,
    Scenario,
    ScenarioContext,
    ScenarioOpinion,
)
from optimizer.baseline import (
    P_BASE,
    option_b_expected_return,
    option_b_probabilities,
)

AS_OF = date(2026, 8, 10)


def _bb(stance: str, confidences: list[str]) -> BullBearOpinion:
    return BullBearOpinion(
        symbol="AAPL", stance=stance, as_of_date=AS_OF, summary="s",  # type: ignore[arg-type]
        arguments=[
            Argument(claim="c", evidence="e", confidence=c)  # type: ignore[arg-type]
            for c in confidences
        ],
        key_risks_to_thesis=["r"], model="m",
        input_tokens=0, output_tokens=0, cost_usd=0.0,
    )


def _trigger() -> InvalidationTrigger:
    return InvalidationTrigger(
        metric="revenue_yoy", direction="less_than", threshold=5.0,
        threshold_unit="percent", description="rev growth below 5pct",
    )


def _opinion() -> ScenarioOpinion:
    return ScenarioOpinion(
        symbol="AAPL", as_of_date=AS_OF,
        scenarios=[
            Scenario(label="bull", probability=0.6, narrative="bull case from Bull #1 margins", invalidation_trigger=_trigger()),
            Scenario(label="base", probability=0.3, narrative="base case from steady fundamentals", invalidation_trigger=_trigger()),
            Scenario(label="bear", probability=0.1, narrative="bear case from Bear #2 demand risk", invalidation_trigger=_trigger()),
        ],
        model="m", input_tokens=0, output_tokens=0, cost_usd=0.0,
    )


def _ctx(bull: BullBearOpinion, bear: BullBearOpinion) -> ScenarioContext:
    return ScenarioContext(
        symbol="AAPL", as_of_date=AS_OF,
        bull_opinion=bull, bear_opinion=bear,
        current_price=100.0, ttm_eps=10.0, peer_pe=[10.0, 12.0, 14.0],
        return_52w_high=0.3, return_52w_low=-0.2,
        run_id="r", scenario_s3_key="scenarios/dt=2026-08-10/symbol=AAPL.json",
        bullbear_s3_keys={"bull": "kb", "bear": "kr"},
    )


def test_probabilities_balanced_when_equal_confidence() -> None:
    p = option_b_probabilities(
        _bb("bull", ["high"] * 3), _bb("bear", ["high"] * 3)
    )
    assert p["base"] == P_BASE
    assert p["bull"] == pytest.approx(p["bear"]) == pytest.approx((1 - P_BASE) / 2)
    assert sum(p.values()) == pytest.approx(1.0)


def test_probabilities_tilt_toward_stronger_side() -> None:
    # bull: high×3=9 vs bear: low×3=3 → p_bull = 9/12 × 0.66
    p = option_b_probabilities(
        _bb("bull", ["high"] * 3), _bb("bear", ["low"] * 3)
    )
    assert p["bull"] == pytest.approx(0.75 * (1 - P_BASE))
    assert p["bear"] == pytest.approx(0.25 * (1 - P_BASE))
    assert sum(p.values()) == pytest.approx(1.0)


def test_option_b_expected_return_replaces_probabilities_only() -> None:
    cfg = ScenarioPricingConfig()
    bull, bear = _bb("bull", ["high"] * 3), _bb("bear", ["low"] * 3)
    ctx = _ctx(bull, bear)
    op = _opinion()

    er_b = option_b_expected_return(op, ctx, cfg)
    # 가격은 옵션 C 와 동일 (같은 ctx·cfg — §6 "확률만 교체")
    from agents.scenario.pricing import compute_expected_return
    er_c = compute_expected_return(op, ctx, cfg)
    assert er_b.scenario_prices == er_c.scenario_prices
    # 확률이 달라졌으므로 expected_return 은 다름 (bull 우세 → 상향)
    assert er_b.expected_return != pytest.approx(er_c.expected_return)
    # 원본 opinion 불변 (사본에만 주입)
    assert op.scenarios[0].probability == 0.6
