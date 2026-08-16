"""MV 최적화 + CHARTER 제약 + 후처리 + 현금 규칙 — 순수 함수 (04 §3~§4).

제약 의미론 (v0.2 구현 확정): CHARTER §3.2 의 3%/15%/35% 는 모두
**최종(총자산 대비) 비중** 기준. MV 는 후보군 내 Σw=1 로 풀므로,
투자비중(invest = min(1, n/10), §4.5) 이 1 미만일 때는 MV 공간의
bound 를 (기준값 / invest) 로 환산해 적용한 뒤 invest 로 스케일한다
— 최종 비중이 항상 CHARTER 범위를 지키도록.

solver infeasible 시 완화 순서 (04 §8): 종목 상한 15→20% (최종 기준)
1회만. 섹터 35% 는 완화 불가 (CHARTER 명시).
"""
from __future__ import annotations

import logging

import pandas as pd
from pypfopt.efficient_frontier import EfficientFrontier
from pypfopt.exceptions import OptimizationError

from optimizer.schemas import (
    MAX_POSITION_WEIGHT,
    MAX_SECTOR_WEIGHT,
    MIN_POSITION_WEIGHT,
)

__all__ = ["investment_fraction", "solve_target_weights", "portfolio_stats"]

logger = logging.getLogger(__name__)

MAX_POSITIONS = 15          # CHARTER §3.2 상한 (하한 10 은 §4.4 규칙 3 — 강제하지 않음)
RELAXED_MAX_POSITION = 0.20  # infeasible 완화 1회 (04 §8)
_CUT_ITERATIONS = 3          # §4.4 — 3% 컷 → 재최적화 반복 상한


def investment_fraction(n_candidates: int) -> float:
    """§4.5 현금 규칙 — 후보 10개 이상이면 완전 투자, 미만이면 n/10."""
    return min(1.0, n_candidates / 10)


def _solve_once(
    mu: pd.Series,
    cov: pd.DataFrame,
    sectors: dict[str, str],
    upper: float,
    sector_upper: float,
) -> dict[str, float]:
    ef = EfficientFrontier(mu, cov.loc[mu.index, mu.index], weight_bounds=(0.0, upper))
    uniq = set(sectors[s] for s in mu.index)
    ef.add_sector_constraints(
        {s: sectors[s] for s in mu.index},
        {k: 0.0 for k in uniq},
        {k: sector_upper for k in uniq},
    )
    ef.max_sharpe(risk_free_rate=0.0)
    return {s: w for s, w in ef.clean_weights().items() if w > 1e-4}


def solve_target_weights(
    mu: pd.Series,
    cov: pd.DataFrame,
    sectors: dict[str, str],
    *,
    invest: float,
) -> dict[str, float]:
    """후보군 (mu/cov/sectors) → 최종(총자산 대비) 비중. 빈 후보면 {}.

    반환 비중 보장: MIN ≤ w ≤ MAX (완화 시 RELAXED), 섹터 ≤ 35%,
    Σw = invest, 종목 수 ≤ 15.
    """
    if len(mu) == 0 or invest <= 0:
        return {}

    max_pos = MAX_POSITION_WEIGHT
    for attempt in range(2):  # 기본 → 완화(20%) 1회
        upper = min(1.0, max_pos / invest)
        sector_upper = min(1.0, MAX_SECTOR_WEIGHT / invest)
        min_w = MIN_POSITION_WEIGHT / invest
        try:
            symbols = list(mu.index)
            weights: dict[str, float] = {}
            for _ in range(_CUT_ITERATIONS):
                weights = _solve_once(
                    mu[symbols], cov, sectors, upper, sector_upper
                )
                keep = [s for s, w in weights.items() if w >= min_w]
                if len(keep) == len(weights):
                    break
                if not keep:  # 전부 min 미만 — 비중 상위 1개는 유지 (수렴 보장)
                    keep = [max(weights, key=weights.get)]  # type: ignore[arg-type]
                symbols = keep
            if len(weights) > MAX_POSITIONS:  # §4.4 규칙 2
                symbols = sorted(weights, key=weights.get, reverse=True)[:MAX_POSITIONS]  # type: ignore[arg-type]
                weights = _solve_once(mu[symbols], cov, sectors, upper, sector_upper)
            return {s: w * invest for s, w in weights.items()}
        except OptimizationError:
            if attempt == 0:
                logger.warning(
                    "MV infeasible — 종목 상한 %.0f%%→%.0f%% 완화 재시도 (04 §8)",
                    max_pos * 100, RELAXED_MAX_POSITION * 100,
                )
                max_pos = RELAXED_MAX_POSITION
            else:
                raise
    raise AssertionError("unreachable")


def portfolio_stats(
    final_weights: dict[str, float], mu: pd.Series, cov: pd.DataFrame
) -> tuple[float, float]:
    """최종 비중 기준 (wᵀμ, wᵀΣw) — 현금 기여 0."""
    if not final_weights:
        return 0.0, 0.0
    w = pd.Series(final_weights)
    er = float((w * mu[w.index]).sum())
    var = float(w.values @ cov.loc[w.index, w.index].values @ w.values)
    return er, var
