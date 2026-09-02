"""rebalancer.schemas 단위 테스트 (05 §2.2)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from rebalancer.schemas import (
    AccountState,
    Position,
    RebalanceSnapshot,
    TradeOrder,
)


# ---------- 헬퍼 ----------


def _order(**over) -> TradeOrder:
    base = dict(
        symbol="EIX", side="buy", shares=21.3245, ref_price=70.17,
        notional=21.3245 * 70.17, reason="rebalance",
    )
    base.update(over)
    return TradeOrder(**base)


def _state(**over) -> AccountState:
    base = dict(
        account_id="primary",
        as_of_date=date(2026, 9, 7),
        cash=1000.0,
        positions={"EIX": Position(shares=21.3245, avg_cost=70.17)},
        inception_date=date(2026, 8, 17),
    )
    base.update(over)
    return AccountState(**base)


def _snapshot(**over) -> RebalanceSnapshot:
    post = _state()
    base = dict(
        account_id="primary",
        as_of_date=date(2026, 9, 7),
        pre_state=_state(as_of_date=date(2026, 8, 31), cash=2496.35, positions={}),
        trades=[_order()],
        post_state=post,
        nav=post.cash + 21.3245 * 70.17,
        prices_used={"EIX": 70.17},
        target_dt=date(2026, 9, 7),
        no_trade_band=0.015,
        computed_at=datetime.now(timezone.utc),
    )
    base.update(over)
    return RebalanceSnapshot(**base)


# ---------- TradeOrder ----------


def test_order_roundtrip() -> None:
    o = _order()
    assert TradeOrder.model_validate_json(o.model_dump_json()) == o


def test_order_notional_must_match_shares_times_price() -> None:
    with pytest.raises(ValidationError, match="notional"):
        _order(notional=100.0)


def test_order_shares_over_four_decimals_rejected() -> None:
    with pytest.raises(ValidationError, match="소수"):
        _order(shares=1.23456, notional=1.23456 * 70.17)


def test_order_zero_or_negative_shares_rejected() -> None:
    with pytest.raises(ValidationError):
        _order(shares=0.0, notional=0.0)


def test_order_reason_side_consistency() -> None:
    with pytest.raises(ValidationError, match="new_position"):
        _order(side="sell", reason="new_position")
    with pytest.raises(ValidationError, match="liquidate_all"):
        _order(side="buy", reason="liquidate_all")
    # 정상 조합
    _order(side="sell", reason="exit_position")


# ---------- AccountState ----------


def test_state_roundtrip() -> None:
    s = _state()
    assert AccountState.model_validate_json(s.model_dump_json()) == s


def test_state_negative_cash_rejected() -> None:
    # 무레버리지 불변식 (§3.4)
    with pytest.raises(ValidationError):
        _state(cash=-0.01)


def test_state_zero_share_position_rejected() -> None:
    # 미보유는 dict 부재로 표현 — shares=0 포지션 금지
    with pytest.raises(ValidationError):
        _state(positions={"EIX": Position(shares=0.0, avg_cost=70.17)})


def test_state_as_of_before_inception_rejected() -> None:
    with pytest.raises(ValidationError, match="inception_date"):
        _state(as_of_date=date(2026, 8, 10))


def test_state_unknown_account_id_rejected() -> None:
    with pytest.raises(ValidationError):
        _state(account_id="shadow")


# ---------- RebalanceSnapshot ----------


def test_snapshot_roundtrip() -> None:
    snap = _snapshot()
    assert RebalanceSnapshot.model_validate_json(snap.model_dump_json()) == snap


def test_snapshot_account_id_mismatch_rejected() -> None:
    with pytest.raises(ValidationError, match="account_id 불일치"):
        _snapshot(pre_state=_state(account_id="option_b",
                                   as_of_date=date(2026, 8, 31), positions={}))


def test_snapshot_post_date_must_match() -> None:
    with pytest.raises(ValidationError, match="as_of_date"):
        _snapshot(post_state=_state(as_of_date=date(2026, 8, 31)))


def test_snapshot_missing_price_for_held_symbol_rejected() -> None:
    with pytest.raises(ValidationError, match="가격 결측"):
        _snapshot(prices_used={})


def test_snapshot_nav_mismatch_rejected() -> None:
    with pytest.raises(ValidationError, match="nav"):
        _snapshot(nav=9999.0)


def test_snapshot_hold_week_no_trades() -> None:
    # G1 보유 유지 주 (§5): 매매 0 + target_dt None — 성과 시계열은 유지
    pre = _state(as_of_date=date(2026, 8, 31))
    post = _state()
    snap = _snapshot(pre_state=pre, post_state=post, trades=[],
                     target_dt=None, weekly_return=-0.012,
                     spy_weekly_return=0.004)
    assert snap.trades == [] and snap.target_dt is None
