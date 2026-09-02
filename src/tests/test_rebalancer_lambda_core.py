"""rebalancer.pricing / lambda_core 목 테스트 (05 §4·§5·§7).

S3 를 dict 로 목 — 기존 optimizer fake store 패턴 재사용. AWS/LLM 호출 없음.
"""
from __future__ import annotations

import json

import pyarrow as pa
import pytest

from rebalancer import lambda_core, pricing
from rebalancer.pricing import PriceMissingError, StalePriceError

DT = "2026-09-07"
PREV_DT = "2026-08-31"


def _ohlcv(price: float, dates: list[str] | None = None) -> pa.Table:
    dates = dates or ["2026-09-03", "2026-09-04"]
    return pa.table({"date": dates, "adj_close": [price * 0.99, price][-len(dates):]})


def _target_bundle(primary: dict, option_b: dict | None) -> dict:
    def _tp(weights):
        return {"as_of_date": DT, "weights": weights,
                "cash_weight": round(1 - sum(weights.values()), 6)}
    return {
        "primary": _tp(primary),
        "option_b_baseline": None if option_b is None else _tp(option_b),
    }


@pytest.fixture
def store(monkeypatch) -> dict:
    objects: dict[str, dict] = {
        f"portfolios/dt={DT}/target.json": _target_bundle(
            {"AAA": 0.15, "BBB": 0.10}, {"AAA": 0.12, "BBB": 0.12},
        ),
    }
    tables: dict[str, pa.Table] = {
        "ohlcv/ticker=AAA/data.parquet": _ohlcv(100.0),
        "ohlcv/ticker=BBB/data.parquet": _ohlcv(40.0),
        "ohlcv/ticker=SPY/data.parquet": _ohlcv(500.0),
    }
    writes: dict[str, str] = {}

    def _read_json(bucket, key):
        if key in writes:
            return json.loads(writes[key])
        return objects.get(key)

    monkeypatch.setattr(pricing, "read_parquet", lambda b, k: tables.get(k))
    monkeypatch.setattr(lambda_core, "read_json", _read_json)
    monkeypatch.setattr(
        lambda_core, "write_text", lambda b, k, t, **kw: writes.update({k: t})
    )
    monkeypatch.setattr(
        lambda_core, "object_exists", lambda b, k: k in writes or k in objects
    )
    monkeypatch.setattr(lambda_core, "_latest_screening_dt", lambda b: DT)
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    return {"objects": objects, "tables": tables, "writes": writes}


# ---------- pricing (G2/G4) ----------


def test_load_prices_asof_picks_last_row(store) -> None:
    from datetime import date
    prices = pricing.load_prices("b", ["AAA"], date(2026, 9, 7))
    assert prices["AAA"] == 100.0
    # as_of 를 과거로 — 그 이전 마지막 행 (리플레이 경로 §4.1)
    prices = pricing.load_prices("b", ["AAA"], date(2026, 9, 3))
    assert prices["AAA"] == pytest.approx(99.0)


def test_load_prices_missing_symbol_raises(store) -> None:
    from datetime import date
    with pytest.raises(PriceMissingError, match="ZZZ"):
        pricing.load_prices("b", ["AAA", "ZZZ"], date(2026, 9, 7))


def test_load_prices_stale_raises(store) -> None:
    from datetime import date
    store["tables"]["ohlcv/ticker=AAA/data.parquet"] = _ohlcv(
        100.0, dates=["2026-08-20", "2026-08-21"]
    )
    with pytest.raises(StalePriceError, match="AAA"):
        pricing.load_prices("b", ["AAA"], date(2026, 9, 7))


# ---------- lambda_core ----------


def test_first_run_initializes_and_rebalances_both_accounts(store) -> None:
    out = lambda_core.handle({}, None)
    assert out["status"] == "ok" and out["dt"] == DT
    by_id = {a["account_id"]: a for a in out["accounts"]}
    assert by_id["primary"]["status"] == "rebalanced"
    assert by_id["option_b"]["status"] == "rebalanced"
    assert by_id["primary"]["weekly_return"] is None          # 첫 주
    assert by_id["primary"]["nav"] == pytest.approx(10_000.0)  # 비용 0

    # 쓰기: 계좌별 snapshot + state (§2.2)
    w = store["writes"]
    snap = json.loads(w[f"accounts/primary/dt={DT}/snapshot.json"])
    state = json.loads(w["accounts/primary/state.json"])
    assert snap["target_dt"] == DT and len(snap["trades"]) == 2
    assert state["positions"].keys() == {"AAA", "BBB"}
    assert state["inception_date"] == DT


def test_idempotent_skip_on_existing_snapshot(store) -> None:
    lambda_core.handle({}, None)
    out2 = lambda_core.handle({}, None)                       # 같은 dt 재실행 (§4.3)
    assert all(a["status"] == "skipped_existing_snapshot" for a in out2["accounts"])


def test_hold_week_when_target_missing(store) -> None:
    lambda_core.handle({}, None)                              # 1주차 정상
    del store["objects"][f"portfolios/dt={DT}/target.json"]
    next_dt = "2026-09-14"
    store["tables"]["ohlcv/ticker=AAA/data.parquet"] = _ohlcv(
        110.0, dates=["2026-09-10", "2026-09-11"]
    )
    store["tables"]["ohlcv/ticker=BBB/data.parquet"] = _ohlcv(
        40.0, dates=["2026-09-10", "2026-09-11"]
    )
    store["tables"]["ohlcv/ticker=SPY/data.parquet"] = _ohlcv(
        505.0, dates=["2026-09-04", "2026-09-11"]
    )
    out = lambda_core.handle({"dt": next_dt}, None)           # G1 보유 유지 (§5)
    p = {a["account_id"]: a for a in out["accounts"]}["primary"]
    assert p["status"] == "hold" and p["n_trades"] == 0
    snap = json.loads(store["writes"][f"accounts/primary/dt={next_dt}/snapshot.json"])
    assert snap["target_dt"] is None
    # 성과 시계열 유지: AAA +10% × 비중 15% ≈ +1.5%
    assert p["weekly_return"] == pytest.approx(0.015, abs=2e-3)
    assert p["spy_weekly_return"] is not None


def test_option_b_holds_when_baseline_null(store) -> None:
    store["objects"][f"portfolios/dt={DT}/target.json"] = _target_bundle(
        {"AAA": 0.15}, None,
    )
    out = lambda_core.handle({}, None)
    by_id = {a["account_id"]: a for a in out["accounts"]}
    assert by_id["primary"]["status"] == "rebalanced"
    assert by_id["option_b"]["status"] == "hold"              # 계좌별 G1 (§3.6)


def test_missing_price_fails_run_without_state_write(store) -> None:
    del store["tables"]["ohlcv/ticker=BBB/data.parquet"]
    with pytest.raises(PriceMissingError):
        lambda_core.handle({}, None)                          # G2 — 런 실패 (§5)
    assert "accounts/primary/state.json" not in store["writes"]
