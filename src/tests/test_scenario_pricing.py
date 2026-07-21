"""시나리오 가격 산정 순수 함수 단위 테스트.

네트워크/S3/LLM 호출 없음. 설계 근거: docs/03-scenario.md §4.1
"""
from __future__ import annotations

from datetime import date as date_cls
from types import SimpleNamespace

import pytest

from agents.bull_bear.schemas import Argument, BullBearOpinion
from agents.scenario.pricing import (
    _apply_probability_cap,
    _validate_price_order,
    combine,
    compute_base_price,
    compute_bear_price,
    compute_bull_price,
    compute_expected_return,
    percentile,
)
from agents.scenario.pricing_config import ScenarioPricingConfig
from agents.scenario.schemas import (
    InvalidationTrigger,
    Scenario,
    ScenarioContext,
    ScenarioOpinion,
)

AS_OF = date_cls(2026, 5, 4)


# ---------- percentile (numpy 'linear' 동등) ----------


@pytest.mark.parametrize(
    "xs, q, expected",
    [
        ([10, 15, 20, 25, 30], 75, 25.0),
        ([10, 15, 20, 25, 30], 50, 20.0),
        ([10, 15, 20, 25, 30], 25, 15.0),
        ([12.0], 75, 12.0),          # singleton
        ([10, 20], 75, 17.5),        # 2-element interp
        ([8, 11, 14, 30, 45], 25, 11.0),
    ],
)
def test_percentile_matches_numpy_linear(xs: list[float], q: float, expected: float) -> None:
    assert percentile(xs, q) == pytest.approx(expected)


def test_percentile_unsorted_input() -> None:
    # 정렬 무관 (코드가 정렬)
    assert percentile([30, 10, 20, 25, 15], 50) == pytest.approx(20.0)


# ---------- combine: 3 mode × is_bear 분기 ----------


def test_combine_bull_modes() -> None:
    assert combine(130, 140, "conservative") == 130  # min (작은 상승)
    assert combine(130, 140, "aggressive") == 140    # max
    assert combine(130, 140, "balanced") == 135      # 평균


def test_combine_bear_modes() -> None:
    # bear: conservative=max(작은 하락), aggressive=min(큰 하락)
    assert combine(80, 100, "conservative", is_bear=True) == 100
    assert combine(80, 100, "aggressive", is_bear=True) == 80
    assert combine(80, 100, "balanced", is_bear=True) == 90


def test_combine_single_none() -> None:
    assert combine(None, 140, "conservative") == 140
    assert combine(130, None, "conservative") == 130


def test_combine_both_none() -> None:
    assert combine(None, None, "conservative") is None


# ---------- compute_*_price ----------

DEFAULT_CFG = ScenarioPricingConfig()
PEER = [8.0, 10.0, 12.0, 14.0, 16.0]  # percentile: p75=14, p50=12, p25=10


def test_compute_bull_price_conservative() -> None:
    # historical=100*1.3=130, peer=p75(14)*10=140, conservative=min=130
    price = compute_bull_price(100.0, 0.3, 10.0, PEER, DEFAULT_CFG)
    assert price == pytest.approx(130.0)


def test_compute_base_price_capped_at_current() -> None:
    # peer=p50(12)*10=120, cap=100*(1+0.0)=100 → min=100
    price = compute_base_price(100.0, 10.0, PEER, DEFAULT_CFG)
    assert price == pytest.approx(100.0)


def test_compute_base_price_no_cap() -> None:
    cfg = ScenarioPricingConfig(base_price_cap_pct=None)
    price = compute_base_price(100.0, 10.0, PEER, cfg)
    assert price == pytest.approx(120.0)


def test_compute_bear_price_conservative() -> None:
    # historical=100*0.8=80, peer=p25(10)*10=100, bear conservative=max=100
    price = compute_bear_price(100.0, -0.2, 10.0, PEER, DEFAULT_CFG)
    assert price == pytest.approx(100.0)


def test_compute_bull_price_both_inputs_missing_fallback() -> None:
    # 52w=None + ttm_eps=None → 양쪽 None → current_price fallback (v0.13)
    price = compute_bull_price(100.0, None, None, [], DEFAULT_CFG)
    assert price == 100.0


def test_compute_bear_price_both_inputs_missing_fallback() -> None:
    price = compute_bear_price(100.0, None, None, [], DEFAULT_CFG)
    assert price == 100.0


def test_compute_bear_price_cap_blocks_inversion() -> None:
    # 딥밸류 케이스: peer=p25(10)*20=200 ≫ 현재가 100, conservative=max → 200 (역전)
    # bear_price_cap_pct=0.0 → min(200, 100*(1+0.0)) = 100 (v0.16)
    uncapped = compute_bear_price(100.0, -0.2, 20.0, PEER, DEFAULT_CFG)
    assert uncapped == pytest.approx(200.0)  # 기본 None = 기존 동작 (역전 재현)
    cfg = ScenarioPricingConfig(bear_price_cap_pct=0.0)
    capped = compute_bear_price(100.0, -0.2, 20.0, PEER, cfg)
    assert capped == pytest.approx(100.0)


def test_compute_bear_price_cap_no_effect_below_current() -> None:
    # 정상 케이스(하락)에는 cap 이 개입하지 않음: max(80, p25(10)*8=80)=80 < 100
    cfg = ScenarioPricingConfig(bear_price_cap_pct=0.0)
    price = compute_bear_price(100.0, -0.2, 8.0, PEER, cfg)
    assert price == pytest.approx(80.0)


def test_compute_bear_price_cap_applies_to_fallback_noop() -> None:
    # 양쪽 결측 fallback(current) 은 cap(current) 과 동일 — 변화 없음
    cfg = ScenarioPricingConfig(bear_price_cap_pct=0.0)
    assert compute_bear_price(100.0, None, None, [], cfg) == 100.0


def test_compute_base_price_missing_peer_fallback() -> None:
    assert compute_base_price(100.0, None, [], DEFAULT_CFG) == 100.0


def test_compute_price_eps_missing_uses_historical_only() -> None:
    # ttm_eps=None → peer 불가, historical 만 → bull = 100*1.3 = 130
    price = compute_bull_price(100.0, 0.3, None, PEER, DEFAULT_CFG)
    assert price == pytest.approx(130.0)


# ---------- _apply_probability_cap ----------


def _probs(bull: float, base: float, bear: float) -> dict[str, SimpleNamespace]:
    return {
        "bull": SimpleNamespace(probability=bull),
        "base": SimpleNamespace(probability=base),
        "bear": SimpleNamespace(probability=bear),
    }


def test_prob_cap_disabled_passthrough() -> None:
    p = _apply_probability_cap(_probs(0.7, 0.2, 0.1), ScenarioPricingConfig())
    assert p == {"bull": 0.7, "base": 0.2, "bear": 0.1}


def test_prob_cap_bull_proportional_redistribution() -> None:
    cfg = ScenarioPricingConfig(bull_probability_cap=0.5)
    p = _apply_probability_cap(_probs(0.7, 0.2, 0.1), cfg)
    assert p["bull"] == pytest.approx(0.5)
    assert p["base"] == pytest.approx(0.3333, abs=1e-3)  # 0.2 + 0.2*(0.2/0.3)
    assert p["bear"] == pytest.approx(0.1667, abs=1e-3)  # 0.1 + 0.2*(0.1/0.3)
    assert sum(p.values()) == pytest.approx(1.0)


def test_prob_cap_bear() -> None:
    cfg = ScenarioPricingConfig(bear_probability_cap=0.2)
    p = _apply_probability_cap(_probs(0.3, 0.3, 0.4), cfg)
    assert p["bear"] == pytest.approx(0.2)
    assert sum(p.values()) == pytest.approx(1.0)


def test_prob_cap_below_threshold_no_change() -> None:
    cfg = ScenarioPricingConfig(bull_probability_cap=0.8)
    p = _apply_probability_cap(_probs(0.7, 0.2, 0.1), cfg)
    assert p["bull"] == 0.7  # 0.7 < 0.8, 변경 없음


# ---------- _validate_price_order ----------


def test_price_order_valid_no_flag() -> None:
    assert _validate_price_order({"bull": 130, "base": 100, "bear": 80}, "AAPL") == []


def test_price_order_violation_flagged() -> None:
    flags = _validate_price_order({"bull": 90, "base": 100, "bear": 80}, "AAPL")
    assert len(flags) == 1
    assert "price_order_violation" in flags[0]


def test_price_order_equal_boundary_ok() -> None:
    assert _validate_price_order({"bull": 100, "base": 100, "bear": 100}, "X") == []


# ---------- compute_expected_return (통합 + 수치) ----------


def _bb(stance: str) -> BullBearOpinion:
    return BullBearOpinion(
        symbol="AAPL", stance=stance, as_of_date=AS_OF, summary="s",  # type: ignore[arg-type]
        arguments=[Argument(claim="c", evidence="e", confidence="high")] * 3,
        key_risks_to_thesis=["r"], model="m",
        input_tokens=0, output_tokens=0, cost_usd=0.0,
    )


def _trigger() -> InvalidationTrigger:
    return InvalidationTrigger(
        metric="revenue_yoy", direction="less_than", threshold=5.0,
        threshold_unit="percent", description="rev growth below 5pct",
    )


def _opinion(pb: float, pba: float, pbe: float) -> ScenarioOpinion:
    return ScenarioOpinion(
        symbol="AAPL", as_of_date=AS_OF,
        scenarios=[
            Scenario(label="bull", probability=pb, narrative="bull case from Bull #1 margins", invalidation_trigger=_trigger()),
            Scenario(label="base", probability=pba, narrative="base case from steady fundamentals", invalidation_trigger=_trigger()),
            Scenario(label="bear", probability=pbe, narrative="bear case from Bear #2 demand risk", invalidation_trigger=_trigger()),
        ],
        model="claude-sonnet-4-6", input_tokens=3000, output_tokens=500, cost_usd=0.018,
    )


def _ctx() -> ScenarioContext:
    return ScenarioContext(
        symbol="AAPL", as_of_date=AS_OF,
        bull_opinion=_bb("bull"), bear_opinion=_bb("bear"),
        current_price=100.0, ttm_eps=10.0, peer_pe=PEER,
        return_52w_high=0.3, return_52w_low=-0.2,
        run_id="r", scenario_s3_key="scenarios/dt=2026-05-04/symbol=AAPL.json",
        bullbear_s3_keys={"bull": "kb", "bear": "kr"},
    )


def test_compute_expected_return_math() -> None:
    # prices: bull=130, base=100, bear=100 (위 단위 테스트 참조)
    # probs: 0.4/0.45/0.15 → expected = 0.4*130+0.45*100+0.15*100 = 112
    er = compute_expected_return(_opinion(0.4, 0.45, 0.15), _ctx(), DEFAULT_CFG)
    assert er.scenario_prices == {"bull": pytest.approx(130.0), "base": pytest.approx(100.0), "bear": pytest.approx(100.0)}
    assert er.expected_price == pytest.approx(112.0)
    assert er.expected_return == pytest.approx(0.12)
    # variance = 0.4*18^2 + 0.45*12^2 + 0.15*12^2 = 129.6+64.8+21.6 = 216
    assert er.variance == pytest.approx(216.0)
    assert er.data_quality_flags == []
    assert er.pricing_config == DEFAULT_CFG
    assert er.scenario_opinion_s3_key == "scenarios/dt=2026-05-04/symbol=AAPL.json"


def test_compute_expected_return_with_cap_affects_weighting() -> None:
    cfg = ScenarioPricingConfig(bull_probability_cap=0.3)
    er = compute_expected_return(_opinion(0.5, 0.3, 0.2), _ctx(), cfg)
    # bull 0.5→0.3, 잉여 0.2 → base/bear 비례. expected 가 base/bear 쪽으로 이동
    # cap 없을 때 expected = 0.5*130+0.3*100+0.2*100 = 115
    # cap 후 bull=0.3 → expected < 115
    assert er.expected_price < 115.0
