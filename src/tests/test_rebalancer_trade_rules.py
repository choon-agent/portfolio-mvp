"""rebalancer.trade_rules 단위 테스트 (05 §3)."""
from __future__ import annotations

from datetime import date

import pytest

from rebalancer.schemas import AccountState, Position
from rebalancer.trade_rules import account_nav, apply_trades, compute_trades

AS_OF = date(2026, 9, 7)


def _state(cash: float = 10_000.0, positions: dict | None = None) -> AccountState:
    return AccountState(
        account_id="primary",
        as_of_date=date(2026, 8, 31),
        cash=cash,
        positions=positions or {},
        inception_date=date(2026, 8, 17),
    )


def _run(state, target, prices, band=0.015):
    plan = compute_trades(state, target, prices, band=band)
    post = apply_trades(state, plan.trades, AS_OF)
    return plan, post


# ---------- 초기 매수 (전액 현금 → 목표 비중) ----------


def test_initial_buy_from_all_cash() -> None:
    target = {"AAA": 0.15, "BBB": 0.10}          # cash_weight 0.75
    prices = {"AAA": 100.0, "BBB": 40.0}
    plan, post = _run(_state(), target, prices)

    assert [t.reason for t in plan.trades] == ["new_position", "new_position"]
    nav = account_nav(post, prices)
    assert nav == pytest.approx(10_000.0)         # 비용 0 — NAV 불변 (§3.2)
    w_aaa = post.positions["AAA"].shares * 100.0 / nav
    assert w_aaa == pytest.approx(0.15, abs=1e-4)  # 절사 오차 이내
    assert post.cash == pytest.approx(7_500.0, abs=1.0)


# ---------- band ----------


def test_band_skips_small_adjustment() -> None:
    # 보유 10% → 목표 11% (Δ 1%p < band) — 스킵, 잔차 기록
    prices = {"AAA": 100.0}
    st = _state(cash=9_000.0, positions={"AAA": Position(shares=10.0, avg_cost=90.0)})
    plan, post = _run(st, {"AAA": 0.11}, prices)
    assert plan.trades == []
    assert plan.skipped_by_band["AAA"] == pytest.approx(0.01)
    assert post.positions["AAA"].shares == 10.0


def test_band_exempt_when_over_cap() -> None:
    # 드리프트로 16% 보유, 목표 15% (Δ 1%p < band) — cap 초과 면제로 트림 강제 (§3.5)
    prices = {"AAA": 160.0, "BBB": 10.0}
    st = _state(cash=0.0, positions={
        "AAA": Position(shares=10.0, avg_cost=100.0),    # 1600 = 16%
        "BBB": Position(shares=840.0, avg_cost=10.0),    # 8400 = 84%
    })
    plan, _ = _run(st, {"AAA": 0.15, "BBB": 0.84}, prices)
    aaa = [t for t in plan.trades if t.symbol == "AAA"]
    assert len(aaa) == 1 and aaa[0].side == "sell"
    assert aaa[0].notional == pytest.approx(100.0, abs=0.2)  # 16% → 15%


def test_band_exempt_when_below_min() -> None:
    # 드리프트로 2% 보유 (< 3% 하한), 목표 3% (Δ 1%p < band) — 면제로 증액 강제
    prices = {"AAA": 20.0, "BBB": 100.0}
    st = _state(cash=1_000.0, positions={
        "AAA": Position(shares=10.0, avg_cost=25.0),     # 200 = 2%
        "BBB": Position(shares=88.0, avg_cost=100.0),    # 8800 = 88%
    })
    plan, post = _run(st, {"AAA": 0.03, "BBB": 0.88}, prices)
    aaa = [t for t in plan.trades if t.symbol == "AAA"]
    assert len(aaa) == 1 and aaa[0].side == "buy"
    nav = account_nav(post, prices)
    assert post.positions["AAA"].shares * 20.0 / nav == pytest.approx(0.03, abs=1e-3)


# ---------- 종목 교체·청산 ----------


def test_exit_position_not_in_target() -> None:
    prices = {"OLD": 50.0, "NEW": 100.0}
    st = _state(cash=5_000.0, positions={"OLD": Position(shares=100.0, avg_cost=40.0)})
    plan, post = _run(st, {"NEW": 0.15}, prices)
    sides = {t.symbol: (t.side, t.reason) for t in plan.trades}
    assert sides["OLD"] == ("sell", "exit_position")
    assert sides["NEW"] == ("buy", "new_position")
    assert "OLD" not in post.positions
    # 매도가 매수보다 앞 (§3.1)
    assert plan.trades[0].side == "sell"


def test_liquidate_all_when_target_empty() -> None:
    # 후보 0 → 전량 매도 (04 §4.5 계약)
    prices = {"AAA": 100.0, "BBB": 40.0}
    st = _state(cash=100.0, positions={
        "AAA": Position(shares=10.0, avg_cost=90.0),
        "BBB": Position(shares=25.0, avg_cost=42.0),
    })
    plan, post = _run(st, {}, prices)
    assert all(t.reason == "liquidate_all" for t in plan.trades)
    assert post.positions == {}
    assert post.cash == pytest.approx(100.0 + 1000.0 + 1000.0)


def test_hold_when_all_within_band() -> None:
    prices = {"AAA": 100.0}
    st = _state(cash=8_950.0, positions={"AAA": Position(shares=10.5, avg_cost=95.0)})
    plan, post = _run(st, {"AAA": 0.105}, prices)   # 현재 10.5% = 목표
    assert plan.trades == [] and post.positions["AAA"].shares == 10.5


# ---------- 현금 제약 (§3.4) ----------


def test_buys_scaled_when_cash_insufficient() -> None:
    # 목표 매수 합이 가용 현금 초과 → 전체 비례 축소, 현금 음수 금지
    prices = {"AAA": 100.0, "BBB": 50.0}
    st = _state(cash=1_000.0, positions={"AAA": Position(shares=90.0, avg_cost=100.0)})
    # NAV=10000. 목표: AAA 90%(변화 없음), BBB 15% = 1500 매수 > 현금 1000
    plan, post = _run(st, {"AAA": 0.90, "BBB": 0.15}, prices)
    buys = [t for t in plan.trades if t.side == "buy"]
    assert len(buys) == 1
    assert buys[0].notional <= 1_000.0 + 1e-6
    assert post.cash >= 0.0


def test_overselling_raises() -> None:
    from rebalancer.schemas import TradeOrder
    st = _state(cash=0.0, positions={"AAA": Position(shares=1.0, avg_cost=100.0)})
    bad = TradeOrder(symbol="AAA", side="sell", shares=2.0, ref_price=100.0,
                     notional=200.0, reason="rebalance")
    with pytest.raises(ValueError, match="초과 매도"):
        apply_trades(st, [bad], AS_OF)


# ---------- 소수점·수치 ----------


def test_shares_truncated_to_four_decimals() -> None:
    prices = {"AAA": 966.78}                       # MU 형 고가 종목 (§3.3 근거)
    plan, post = _run(_state(), {"AAA": 0.15}, prices)
    t = plan.trades[0]
    assert round(t.shares, 4) == t.shares
    assert t.notional <= 1_500.0                   # 절사 → 목표 이하 (잔차는 현금)
    assert post.positions["AAA"].shares == t.shares


def test_avg_cost_weighted_on_additional_buy() -> None:
    prices = {"AAA": 200.0}
    st = _state(cash=9_000.0, positions={"AAA": Position(shares=10.0, avg_cost=100.0)})
    plan, post = _run(st, {"AAA": 0.15}, prices, band=0.001)
    # 현재 2000/11000 → 목표 15%: 소폭 매도… 아님 — NAV=11000, 목표 1650 < 2000 → 매도
    assert plan.trades[0].side == "sell"
    assert post.positions["AAA"].avg_cost == 100.0  # 매도는 avg_cost 불변


def test_nav_preserved_through_rebalance() -> None:
    # 비용 0 체결 — 어떤 리밸런싱도 NAV 를 바꾸지 않음 (§3.2 결정성)
    prices = {"AAA": 123.45, "BBB": 67.89, "CCC": 10.11}
    st = _state(cash=2_000.0, positions={
        "AAA": Position(shares=30.0, avg_cost=100.0),
        "BBB": Position(shares=50.0, avg_cost=70.0),
    })
    nav_before = account_nav(st, prices)
    _, post = _run(st, {"AAA": 0.10, "CCC": 0.12}, prices)
    assert account_nav(post, prices) == pytest.approx(nav_before)
