"""4단계 optimizer Lambda 공유 코어 — 조립·로깅·저장 (04 §7).

흐름:
  1. dt 결정 (event["dt"] 우선, 없으면 최신 파티션)
  2. load_gated_universe (G1/G2/G4) + load_return_matrix (G3)
  3. 후보 = 게이트 통과 AND ER > 0 (§4.5) → invest = min(1, n/10)
  4. 하이브리드 Σ (§4.2) → MV + 제약 + 후처리 (§4.3~4.4) → primary
  5. 옵션 B baseline 동일 파이프라인 (§6) — ER 만 교체
  6. OptimizerBundle → s3://{bucket}/portfolios/dt={dt}/target.json

LLM 호출 0 (CHARTER §3.3). 실행 환경: 컨테이너 이미지 Lambda (04 §7.2).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from common.s3_io import write_text
from optimizer import data_loader
from optimizer.baseline import option_b_expected_return
from optimizer.covariance import (
    hybrid_covariance,
    return_space_variance,
    shrunk_correlation,
)
from optimizer.data_loader import GateResult
from optimizer.optimize import (
    investment_fraction,
    portfolio_stats,
    solve_target_weights,
)
from optimizer.schemas import CovarianceParams, OptimizerBundle, TargetPortfolio

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

DEFAULT_PORTFOLIOS_PREFIX = "portfolios"


def _config() -> dict[str, str]:
    return {
        "bucket": os.environ["S3_BUCKET"],
        "portfolios_prefix": os.environ.get(
            "PORTFOLIOS_PREFIX", DEFAULT_PORTFOLIOS_PREFIX
        ),
    }


def _build_portfolio(
    *,
    er_by_symbol: dict[str, float],
    gate: GateResult,
    returns: pd.DataFrame,
    extra_excluded: dict[str, str],
    params: CovarianceParams,
    now: datetime,
) -> TargetPortfolio:
    """ER 사전 1개 → TargetPortfolio (primary/baseline 공용 경로)."""
    excluded = dict(gate.excluded) | extra_excluded
    candidates = sorted(
        s for s, er in er_by_symbol.items()
        if er > 0 and s not in extra_excluded and s in returns.columns
    )
    for s, er in er_by_symbol.items():
        if s not in excluded and er <= 0:
            excluded[s] = "er_not_positive"

    invest = investment_fraction(len(candidates))
    weights: dict[str, float] = {}
    er_p, var_p = 0.0, 0.0
    if candidates:
        variances = {
            s: return_space_variance(
                gate.passed[s].primary.variance,
                gate.passed[s].ctx.current_price,
                params.var_floor,
            )
            for s in candidates
        }
        corr = shrunk_correlation(returns[candidates], params.shrinkage)
        cov = hybrid_covariance(variances, corr, params)
        mu = pd.Series({s: er_by_symbol[s] for s in candidates})
        sectors = {s: gate.passed[s].ctx.sector or "Unknown" for s in candidates}
        weights = solve_target_weights(mu, cov, sectors, invest=invest)
        er_p, var_p = portfolio_stats(weights, mu, cov)

    return TargetPortfolio(
        as_of_date=datetime.strptime(gate.dt, "%Y-%m-%d").date(),
        weights=weights,
        cash_weight=round(1.0 - sum(weights.values()), 10),
        expected_portfolio_return=er_p,
        portfolio_variance=var_p,
        universe_size=gate.universe_size,
        excluded=excluded,
        n_candidates=len(candidates),
        pricing_config_hash=gate.pricing_config_hash or "n/a",
        covariance_params=params,
        computed_at=now,
    )


def handle(event: dict[str, Any], context: Any) -> dict[str, Any]:
    cfg = _config()
    now = datetime.now(timezone.utc)
    params = CovarianceParams.model_validate(event.get("covariance_params") or {})

    dt = event.get("dt") or data_loader.latest_dt(cfg["bucket"])
    if dt is None:
        raise RuntimeError("expected_returns 파티션 없음 — dt 결정 불가")

    gate = data_loader.load_gated_universe(cfg["bucket"], dt)
    returns, g3_excluded = data_loader.load_return_matrix(
        cfg["bucket"], sorted(gate.passed), params
    )

    primary = _build_portfolio(
        er_by_symbol={s: d.primary.expected_return for s, d in gate.passed.items()},
        gate=gate, returns=returns, extra_excluded=g3_excluded,
        params=params, now=now,
    )

    # 옵션 B baseline (§6) — 같은 게이트·Σ 경로, ER 만 교체. observe-only
    er_b = {
        s: option_b_expected_return(d.opinion, d.ctx, d.primary.pricing_config).expected_return
        for s, d in gate.passed.items()
    }
    baseline = _build_portfolio(
        er_by_symbol=er_b, gate=gate, returns=returns,
        extra_excluded=g3_excluded, params=params, now=now,
    )

    bundle = OptimizerBundle(primary=primary, option_b_baseline=baseline)
    out_key = f"{cfg['portfolios_prefix']}/dt={dt}/target.json"
    write_text(cfg["bucket"], out_key, bundle.model_dump_json())

    top5 = dict(sorted(primary.weights.items(), key=lambda kv: -kv[1])[:5])
    summary = {
        "stage": "completed", "dt": dt, "s3_key": out_key,
        "universe_size": primary.universe_size,
        "n_candidates": primary.n_candidates,
        "n_positions": len(primary.weights),
        "cash_weight": primary.cash_weight,
        "expected_portfolio_return": primary.expected_portfolio_return,
        "portfolio_variance": primary.portfolio_variance,
        "top5": top5,
        "excluded": primary.excluded,
        "baseline_n_positions": len(baseline.weights),
    }
    logger.info(json.dumps(summary, ensure_ascii=False))
    return {"status": "ok", **summary}
