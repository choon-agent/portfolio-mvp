"""optimizer.optimize 단위 테스트 (04 §3~§4)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from optimizer.optimize import (
    investment_fraction,
    portfolio_stats,
    solve_target_weights,
)
from optimizer.schemas import MAX_SECTOR_WEIGHT


def _fixture(n: int = 12, seed: int = 3) -> tuple[pd.Series, pd.DataFrame, dict]:
    """양수 ER n 종목 + 안정적 Σ (대각 우세) fixture."""
    rng = np.random.default_rng(seed)
    syms = [f"S{i:02d}" for i in range(n)]
    mu = pd.Series(rng.uniform(0.01, 0.09, n), index=syms)
    sig = rng.uniform(0.05, 0.15, n)
    rho = 0.3 * np.ones((n, n)) + 0.7 * np.eye(n)
    cov = pd.DataFrame(np.outer(sig, sig) * rho, index=syms, columns=syms)
    sectors = {s: f"sec{i % 4}" for i, s in enumerate(syms)}
    return mu, cov, sectors


def test_investment_fraction_rule() -> None:
    assert investment_fraction(12) == 1.0
    assert investment_fraction(10) == 1.0
    assert investment_fraction(5) == 0.5
    assert investment_fraction(0) == 0.0


def test_full_invest_respects_all_constraints() -> None:
    mu, cov, sectors = _fixture()
    w = solve_target_weights(mu, cov, sectors, invest=1.0)
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-4)
    assert all(0.03 - 1e-6 <= x <= 0.15 + 1e-6 for x in w.values())
    assert len(w) <= 15
    by_sector: dict[str, float] = {}
    for s, x in w.items():
        by_sector[sectors[s]] = by_sector.get(sectors[s], 0) + x
    assert all(v <= MAX_SECTOR_WEIGHT + 1e-6 for v in by_sector.values())


def test_partial_invest_scales_final_weights() -> None:
    mu, cov, sectors = _fixture(n=5)
    w = solve_target_weights(mu, cov, sectors, invest=0.5)
    assert sum(w.values()) == pytest.approx(0.5, abs=1e-4)
    # 최종 비중 기준 3%~15% 유지 (§4.5 스케일 후에도)
    assert all(0.03 - 1e-6 <= x <= 0.15 + 1e-6 for x in w.values())


def test_empty_candidates_returns_empty() -> None:
    mu, cov, sectors = _fixture(n=3)
    assert solve_target_weights(mu.iloc[:0], cov, sectors, invest=0.0) == {}
    assert solve_target_weights(mu.iloc[:0], cov, sectors, invest=1.0) == {}


def test_min_cut_loop_removes_tiny_positions() -> None:
    # 한 종목의 ER 을 극단적으로 낮춰 잔여 비중 유도 → 컷 후 재최적화로 소멸
    mu, cov, sectors = _fixture()
    mu = mu.copy()
    mu.iloc[0] = 0.0001
    w = solve_target_weights(mu, cov, sectors, invest=1.0)
    assert all(x >= 0.03 - 1e-6 for x in w.values())


def test_determinism_same_input_same_output() -> None:
    mu, cov, sectors = _fixture()
    w1 = solve_target_weights(mu, cov, sectors, invest=1.0)
    w2 = solve_target_weights(mu, cov, sectors, invest=1.0)
    assert w1.keys() == w2.keys()
    for s in w1:
        assert w1[s] == pytest.approx(w2[s], abs=1e-6)


def test_portfolio_stats() -> None:
    mu, cov, _ = _fixture(n=2)
    w = {"S00": 0.5, "S01": 0.5}
    er, var = portfolio_stats(w, mu, cov)
    assert er == pytest.approx(0.5 * mu["S00"] + 0.5 * mu["S01"])
    assert var == pytest.approx(
        float(np.array([0.5, 0.5]) @ cov.values @ np.array([0.5, 0.5]))
    )
    assert portfolio_stats({}, mu, cov) == (0.0, 0.0)
