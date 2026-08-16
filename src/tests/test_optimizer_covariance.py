"""optimizer.covariance 단위 테스트 (04 §4.2)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from optimizer.covariance import (
    hybrid_covariance,
    log_returns,
    return_space_variance,
    shrunk_correlation,
)
from optimizer.schemas import CovarianceParams

PARAMS = CovarianceParams()  # 252 / 0.2 / 0.0025


def test_return_space_variance_converts_price_space() -> None:
    # 가격 분산 25 ($5²), 현재가 100 → (5/100)² = 0.0025... 정확히는 25/10000
    assert return_space_variance(25.0, 100.0, 0.0001) == pytest.approx(0.0025)


def test_return_space_variance_floor_catches_degenerate() -> None:
    # ALL 형 퇴화: variance=0 → floor (03 부록 B — riskless 오판 차단)
    assert return_space_variance(0.0, 250.0, PARAMS.var_floor) == PARAMS.var_floor


def test_return_space_variance_rejects_bad_price() -> None:
    with pytest.raises(ValueError):
        return_space_variance(1.0, 0.0, 0.0025)


def test_log_returns_window() -> None:
    prices = pd.Series(100.0 * np.exp(np.arange(300) * 0.001))
    r = log_returns(prices, window=252)
    assert len(r) == 252
    assert r.iloc[-1] == pytest.approx(0.001)


def test_shrunk_correlation_pulls_toward_identity() -> None:
    rng = np.random.default_rng(7)
    base = rng.normal(size=200)
    df = pd.DataFrame({
        "A": base + rng.normal(scale=0.1, size=200),
        "B": base + rng.normal(scale=0.1, size=200),   # A 와 고상관
        "C": rng.normal(size=200),
    })
    raw = df.corr().loc["A", "B"]
    shrunk = shrunk_correlation(df, 0.2).loc["A", "B"]
    assert shrunk == pytest.approx(0.8 * raw)          # 비대각 × (1-λ)
    assert shrunk_correlation(df, 0.2).loc["A", "A"] == pytest.approx(1.0)  # 대각 보존


def test_hybrid_covariance_diagonal_is_scenario_variance() -> None:
    corr = pd.DataFrame(
        [[1.0, 0.5], [0.5, 1.0]], index=["A", "B"], columns=["A", "B"]
    )
    var = {"A": 0.01, "B": 0.04}
    cov = hybrid_covariance(var, corr, PARAMS)
    assert cov.loc["A", "A"] == pytest.approx(0.01)
    assert cov.loc["B", "B"] == pytest.approx(0.04)
    assert cov.loc["A", "B"] == pytest.approx(0.5 * 0.1 * 0.2)


def test_hybrid_covariance_is_psd() -> None:
    # 의도적으로 비PSD 상관 (3종목 ρ=0.99/0.99/-0.99 조합)
    corr = pd.DataFrame(
        [[1.0, 0.99, 0.99], [0.99, 1.0, -0.99], [0.99, -0.99, 1.0]],
        index=list("ABC"), columns=list("ABC"),
    )
    cov = hybrid_covariance({"A": 0.01, "B": 0.01, "C": 0.01}, corr, PARAMS)
    assert np.linalg.eigvalsh(cov.values).min() >= 0


def test_hybrid_covariance_symbol_mismatch_rejected() -> None:
    corr = pd.DataFrame([[1.0]], index=["A"], columns=["A"])
    with pytest.raises(ValueError, match="심볼 불일치"):
        hybrid_covariance({"B": 0.01}, corr, PARAMS)
