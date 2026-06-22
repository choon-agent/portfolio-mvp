"""시나리오 가격 config 단위 테스트.

순수 Pydantic 검증 + env/override 병합 — 네트워크/S3/LLM 호출 없음.
설계 근거: docs/03-scenario.md §4.2, §4.3
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.scenario.pricing_config import (
    ScenarioPricingConfig,
    alternative_configs,
    load_pricing_config,
)


# ---------- 기본값 ----------


def test_defaults_are_conservative() -> None:
    cfg = ScenarioPricingConfig()
    assert cfg.bull_aggressiveness == "conservative"
    assert cfg.bear_conservatism == "conservative"
    assert cfg.peer_pe_bull_percentile == 75.0
    assert cfg.peer_pe_base_percentile == 50.0
    assert cfg.peer_pe_bear_percentile == 25.0
    assert cfg.base_price_cap_pct == 0.0
    assert cfg.bull_probability_cap is None
    assert cfg.bear_probability_cap is None


# ---------- percentile 순서 validator (v0.6) ----------


def test_percentile_order_valid_passes() -> None:
    cfg = ScenarioPricingConfig(
        peer_pe_bear_percentile=20.0,
        peer_pe_base_percentile=50.0,
        peer_pe_bull_percentile=80.0,
    )
    assert cfg.peer_pe_bear_percentile == 20.0


def test_percentile_order_inverted_rejected() -> None:
    # bear=50, base=40 → bear > base (Field bound 안에서도 역전 가능)
    with pytest.raises(ValidationError, match="percentile 순서 위반"):
        ScenarioPricingConfig(
            peer_pe_bear_percentile=50.0,
            peer_pe_base_percentile=40.0,
            peer_pe_bull_percentile=80.0,
        )


def test_percentile_equal_boundary_allowed() -> None:
    # bear == base == bull (≤ 허용) — 경계
    cfg = ScenarioPricingConfig(
        peer_pe_bear_percentile=50.0,
        peer_pe_base_percentile=50.0,
        peer_pe_bull_percentile=50.0,
    )
    assert cfg.peer_pe_base_percentile == 50.0


# ---------- Field bound (v0.6) ----------


@pytest.mark.parametrize("bad", [-0.6, 1.5])
def test_base_price_cap_out_of_bound_rejected(bad: float) -> None:
    with pytest.raises(ValidationError):
        ScenarioPricingConfig(base_price_cap_pct=bad)


def test_base_price_cap_none_allowed() -> None:
    cfg = ScenarioPricingConfig(base_price_cap_pct=None)
    assert cfg.base_price_cap_pct is None


@pytest.mark.parametrize(
    "field, bad",
    [
        ("peer_pe_bull_percentile", 49.0),  # < 50
        ("peer_pe_bull_percentile", 96.0),  # > 95
        ("peer_pe_bear_percentile", 4.0),   # < 5
        ("bull_probability_cap", 1.5),      # > 1
        ("bear_probability_cap", -0.1),     # < 0
    ],
)
def test_field_bounds_rejected(field: str, bad: float) -> None:
    with pytest.raises(ValidationError):
        ScenarioPricingConfig(**{field: bad})


# ---------- load_pricing_config: env/override 병합 (§4.3) ----------


def test_load_defaults_when_empty() -> None:
    cfg = load_pricing_config(env={}, override=None)
    assert cfg == ScenarioPricingConfig()


def test_load_env_override() -> None:
    cfg = load_pricing_config(
        env={
            "SCENARIO_BULL_AGGRESSIVENESS": "aggressive",
            "SCENARIO_PEER_PE_BULL_PERCENTILE": "90",
            "SCENARIO_BULL_PROBABILITY_CAP": "0.6",
        },
        override=None,
    )
    assert cfg.bull_aggressiveness == "aggressive"
    assert cfg.peer_pe_bull_percentile == 90.0
    assert cfg.bull_probability_cap == 0.6
    # 미지정 필드는 기본값
    assert cfg.bear_conservatism == "conservative"


def test_load_env_opt_float_none() -> None:
    cfg = load_pricing_config(
        env={"SCENARIO_BASE_PRICE_CAP_PCT": "none"},
        override=None,
    )
    assert cfg.base_price_cap_pct is None


def test_load_override_wins_over_env() -> None:
    cfg = load_pricing_config(
        env={"SCENARIO_BULL_AGGRESSIVENESS": "aggressive"},
        override={"bull_aggressiveness": "balanced"},
    )
    assert cfg.bull_aggressiveness == "balanced"


def test_load_validates_merged_result() -> None:
    # env+override 병합 후에도 percentile 순서 검증
    with pytest.raises(ValidationError, match="percentile 순서 위반"):
        load_pricing_config(
            env={"SCENARIO_PEER_PE_BEAR_PERCENTILE": "50"},
            override={"peer_pe_base_percentile": 40.0},
        )


def test_load_invalid_literal_rejected() -> None:
    with pytest.raises(ValidationError):
        load_pricing_config(
            env={"SCENARIO_BULL_AGGRESSIVENESS": "reckless"},
            override=None,
        )


# ---------- alternative_configs (#12 sensitivity, §4.4) ----------


def test_alternative_configs_keys() -> None:
    alts = alternative_configs(ScenarioPricingConfig())
    assert set(alts) == {"balanced", "base_cap_10", "aggressive"}


def test_alternative_configs_variants() -> None:
    alts = alternative_configs(ScenarioPricingConfig())
    assert alts["balanced"].bull_aggressiveness == "balanced"
    assert alts["balanced"].bear_conservatism == "balanced"
    assert alts["base_cap_10"].base_price_cap_pct == 0.10
    assert alts["aggressive"].bull_aggressiveness == "aggressive"
    assert alts["aggressive"].base_price_cap_pct == 0.10


def test_alternative_configs_inherit_base() -> None:
    # base 의 percentile 등 미변경 필드는 그대로 상속
    base = ScenarioPricingConfig(peer_pe_bull_percentile=90.0)
    assert alternative_configs(base)["balanced"].peer_pe_bull_percentile == 90.0
