"""optimizer.schemas 단위 테스트 (04 §2.2)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from optimizer.schemas import CovarianceParams, OptimizerBundle, TargetPortfolio


def _tp(**over) -> TargetPortfolio:
    base = dict(
        as_of_date=date(2026, 8, 10),
        weights={"APA": 0.15, "NEE": 0.15, "DVA": 0.10, "MU": 0.06,
                 "EIX": 0.08, "HST": 0.12, "UAL": 0.10, "ALL": 0.10,
                 "MPC": 0.07, "ZBH": 0.07},
        cash_weight=0.0,
        expected_portfolio_return=0.036,
        portfolio_variance=0.003,
        universe_size=20,
        excluded={"DD": "flags"},
        n_candidates=12,
        pricing_config_hash="a" * 16,
        covariance_params=CovarianceParams(),
        computed_at=datetime.now(timezone.utc),
    )
    base.update(over)
    return TargetPortfolio(**base)


def test_valid_portfolio_roundtrip() -> None:
    tp = _tp()
    assert TargetPortfolio.model_validate_json(tp.model_dump_json()) == tp


def test_weights_plus_cash_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="비중 합"):
        _tp(cash_weight=0.5)  # Σw=1.0 + cash 0.5 → 1.5


def test_cash_only_portfolio_allowed() -> None:
    # 후보 0 국면 (§4.5) — 전액 현금
    tp = _tp(weights={}, cash_weight=1.0, n_candidates=0,
             expected_portfolio_return=0.0, portfolio_variance=0.0)
    assert tp.weights == {} and tp.cash_weight == 1.0


def test_position_below_min_rejected() -> None:
    w = {"APA": 0.15, "NEE": 0.15, "DVA": 0.15, "MU": 0.15,
         "EIX": 0.15, "HST": 0.13, "UAL": 0.10, "ZBH": 0.01}  # 1% < 3%
    with pytest.raises(ValidationError, match="범위 위반"):
        _tp(weights=w)


def test_position_above_max_rejected() -> None:
    w = {"APA": 0.20, "NEE": 0.15, "DVA": 0.15, "MU": 0.15,
         "EIX": 0.15, "HST": 0.10, "UAL": 0.10}  # 20% > 15%
    with pytest.raises(ValidationError, match="범위 위반"):
        _tp(weights=w)


def test_partial_invest_scaled_weights_valid() -> None:
    # 후보 5개 → invest 50% (§4.5). 최종 비중도 3%~15% 준수해야 함
    w = {"APA": 0.15, "NEE": 0.12, "DVA": 0.10, "MU": 0.08, "EIX": 0.05}
    tp = _tp(weights=w, cash_weight=0.5, n_candidates=5)
    assert tp.cash_weight == 0.5


def test_bundle_baseline_optional() -> None:
    b = OptimizerBundle(primary=_tp())
    assert b.option_b_baseline is None
    b2 = OptimizerBundle(primary=_tp(), option_b_baseline=_tp())
    assert b2.option_b_baseline is not None


def test_covariance_params_bounds() -> None:
    with pytest.raises(ValidationError):
        CovarianceParams(corr_window_days=10)  # < 60
    with pytest.raises(ValidationError):
        CovarianceParams(shrinkage=1.5)
    with pytest.raises(ValidationError):
        CovarianceParams(var_floor=0.0)
