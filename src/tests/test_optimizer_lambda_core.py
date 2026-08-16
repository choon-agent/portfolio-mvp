"""optimizer.data_loader + lambda_core 테스트 (04 §5, §7) — S3 목."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import numpy as np
import pyarrow as pa
import pytest

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
from optimizer import data_loader, lambda_core
from optimizer.data_loader import ConfigMismatchError
from optimizer.schemas import OptimizerBundle

DT = "2026-08-10"
AS_OF = date(2026, 8, 10)
CFG = ScenarioPricingConfig()
SECTORS = ["Energy", "Utilities", "Health Care", "Financials"]


def _bb(stance: str) -> BullBearOpinion:
    return BullBearOpinion(
        symbol="X", stance=stance, as_of_date=AS_OF, summary="s",  # type: ignore[arg-type]
        arguments=[Argument(claim="c", evidence="e", confidence="medium")] * 3,
        key_risks_to_thesis=["r"], model="m",
        input_tokens=0, output_tokens=0, cost_usd=0.0,
    )


def _trigger() -> InvalidationTrigger:
    return InvalidationTrigger(
        metric="revenue_yoy", direction="less_than", threshold=5.0,
        threshold_unit="percent", description="rev growth below threshold",
    )


def _opinion(sym: str) -> dict:
    op = ScenarioOpinion(
        symbol=sym, as_of_date=AS_OF,
        scenarios=[
            Scenario(label="bull", probability=0.4, narrative="bull case with enough narrative body", invalidation_trigger=_trigger()),
            Scenario(label="base", probability=0.4, narrative="base case with enough narrative body", invalidation_trigger=_trigger()),
            Scenario(label="bear", probability=0.2, narrative="bear case with enough narrative body", invalidation_trigger=_trigger()),
        ],
        model="m", input_tokens=0, output_tokens=0, cost_usd=0.0,
    )
    return {"scenario_opinion": json.loads(op.model_dump_json())}


def _er(sym: str, er: float, *, var: float = 25.0, flags: list[str] | None = None,
        cfg: ScenarioPricingConfig = CFG) -> dict:
    e = ExpectedReturn(
        symbol=sym, as_of_date=AS_OF,
        expected_price=100.0 * (1 + er), expected_return=er, variance=var,
        scenario_prices={"bull": 120.0, "base": 100.0, "bear": 80.0},
        pricing_config=cfg, data_quality_flags=flags or [],
        scenario_opinion_s3_key=f"scenarios/dt={DT}/symbol={sym}.json",
        computed_at=datetime.now(timezone.utc),
    )
    return json.loads(
        ExpectedReturnsBundle(primary=e).model_dump_json()
    )


def _ctx(sym: str, sector: str) -> dict:
    c = ScenarioContext(
        symbol=sym, as_of_date=AS_OF,
        bull_opinion=_bb("bull"), bear_opinion=_bb("bear"),
        current_price=100.0, ttm_eps=10.0, peer_pe=[10.0, 12.0, 14.0],
        return_52w_high=0.2, return_52w_low=-0.2, sector=sector,
        run_id="r", scenario_s3_key=f"scenarios/dt={DT}/symbol={sym}.json",
        bullbear_s3_keys={"bull": "kb", "bear": "kr"},
    )
    return json.loads(c.model_dump_json())


def _ohlcv(seed: int) -> pa.Table:
    rng = np.random.default_rng(seed)
    n = 300
    prices = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    dates = [f"2025-{(i // 25) % 12 + 1:02d}-{i % 25 + 1:02d}" for i in range(n)]
    return pa.table({"date": dates, "adj_close": prices})


@pytest.fixture
def store(monkeypatch) -> dict:
    """4종목 정상 세계 + G1/G2 케이스. read/write 를 dict 로 목."""
    syms = ["AAA", "BBB", "CCC", "DDD"]
    objects: dict[str, dict] = {
        f"screening/dt={DT}/result.json": {
            "as_of_date": DT, "universe_size": 500,
            "selected": [{"symbol": s} for s in syms + ["FLG", "MIS"]],
            "factor_weights": {}, "run_id": "r",
        },
    }
    for i, s in enumerate(syms):
        objects[f"expected_returns/dt={DT}/symbol={s}.json"] = _er(s, 0.02 + 0.02 * i)
        objects[f"scenario_contexts/dt={DT}/symbol={s}.json"] = _ctx(s, SECTORS[i])
        objects[f"scenarios/dt={DT}/symbol={s}.json"] = _opinion(s)
    # G1: flag 종목 / G2: expected_returns 없음(MIS)
    objects[f"expected_returns/dt={DT}/symbol=FLG.json"] = _er(
        "FLG", 0.5, flags=["price_order_violation: x"]
    )

    tables = {f"ohlcv/ticker={s}/data.parquet": _ohlcv(i) for i, s in enumerate(syms)}
    writes: dict[str, str] = {}

    monkeypatch.setattr(data_loader, "read_json", lambda b, k: objects.get(k))
    monkeypatch.setattr(data_loader, "read_parquet", lambda b, k: tables.get(k))
    monkeypatch.setattr(
        lambda_core, "write_text", lambda b, k, t, **kw: writes.update({k: t})
    )
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    return {"objects": objects, "writes": writes}


def test_gates_classify_universe(store) -> None:
    gate = data_loader.load_gated_universe("test-bucket", DT)
    assert gate.universe_size == 6
    assert sorted(gate.passed) == ["AAA", "BBB", "CCC", "DDD"]
    assert gate.excluded["FLG"].startswith("data_quality_flags")   # G1
    assert gate.excluded["MIS"] == "expected_return_missing"       # G2
    assert len(gate.pricing_config_hash) == 16


def test_gate_config_mismatch_fails_run(store) -> None:
    other = ScenarioPricingConfig(peer_pe_bull_percentile=90.0)
    store["objects"][f"expected_returns/dt={DT}/symbol=BBB.json"] = _er(
        "BBB", 0.05, cfg=other
    )
    with pytest.raises(ConfigMismatchError):                       # G4
        data_loader.load_gated_universe("test-bucket", DT)


def test_return_matrix_g3(store, monkeypatch) -> None:
    from optimizer.schemas import CovarianceParams
    monkeypatch.setattr(
        data_loader, "read_parquet",
        lambda b, k: None if "AAA" in k else _ohlcv(1),
    )
    returns, excluded = data_loader.load_return_matrix(
        "test-bucket", ["AAA", "BBB"], CovarianceParams()
    )
    assert excluded == {"AAA": "insufficient_ohlcv"}               # G3
    assert list(returns.columns) == ["BBB"]


def test_handle_end_to_end(store) -> None:
    out = lambda_core.handle({"dt": DT}, None)
    assert out["status"] == "ok"
    assert out["n_candidates"] == 4
    # 후보 4개 → invest 0.4 (§4.5) → 현금 60%
    assert out["cash_weight"] == pytest.approx(0.6, abs=1e-4)

    key = f"portfolios/dt={DT}/target.json"
    bundle = OptimizerBundle.model_validate_json(store["writes"][key])
    assert bundle.primary.excluded["FLG"].startswith("data_quality_flags")
    assert bundle.primary.n_candidates == 4
    assert sum(bundle.primary.weights.values()) == pytest.approx(0.4, abs=1e-4)
    # 옵션 B baseline 병렬 산출 (§6)
    assert bundle.option_b_baseline is not None
    assert bundle.option_b_baseline.pricing_config_hash == bundle.primary.pricing_config_hash


def test_handle_negative_er_goes_to_cash(store) -> None:
    for s in ["AAA", "BBB", "CCC", "DDD"]:
        store["objects"][f"expected_returns/dt={DT}/symbol={s}.json"] = _er(s, -0.05)
    out = lambda_core.handle({"dt": DT}, None)
    assert out["n_candidates"] == 0
    assert out["cash_weight"] == 1.0
    bundle = OptimizerBundle.model_validate_json(
        store["writes"][f"portfolios/dt={DT}/target.json"]
    )
    assert bundle.primary.weights == {}
    assert all(
        v == "er_not_positive"
        for k, v in bundle.primary.excluded.items()
        if k in {"AAA", "BBB", "CCC", "DDD"}
    )
