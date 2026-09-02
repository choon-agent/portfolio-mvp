"""매매 규칙 — 순수 함수 (05 §3, v0.2 확정).

state × target × prices → 매매 목록 + 체결 후 상태. S3/네트워크/LLM 호출 없음.

규칙 요약 (§3.1 순서 고정):
1. NAV 평가 (체결가 = 직전 거래일 adj_close — 평가·체결 동일 가격, §3.2)
2. Δw = target_w − current_w (대상 = 보유 ∪ target)
3. no-trade band |Δw| < 1.5%p 스킵 — 단, 현재 비중이 CHARTER 범위(3~15%) 밖이면
   면제 (항상 체결, §3.5)
4. 매도 먼저 → 매수. 현금 부족 시 매수 전체 비례 축소 (§3.4 무레버리지)
5. target 빈 dict → 전량 매도 (liquidate_all — 04 §4.5 계약)

소수점 주식: 소수 4자리 *절사* (§3.3) — 절사 잔차는 현금이 흡수.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from optimizer.schemas import MAX_POSITION_WEIGHT, MIN_POSITION_WEIGHT
from rebalancer.schemas import (
    DEFAULT_NO_TRADE_BAND,
    SHARE_DECIMALS,
    AccountState,
    Position,
    TradeOrder,
)

__all__ = ["TradePlan", "account_nav", "compute_trades", "apply_trades"]

_SHARE_QUANTUM = 10 ** SHARE_DECIMALS
_EPS = 1e-9


@dataclass(frozen=True)
class TradePlan:
    """compute_trades 결과 — 스냅샷 lineage 용 (§2.2)."""

    trades: list[TradeOrder] = field(default_factory=list)
    skipped_by_band: dict[str, float] = field(default_factory=dict)  # symbol → |Δw|


def _trunc_shares(shares: float) -> float:
    """소수 4자리 절사 (§3.3). 음수 입력 금지 (호출부 책임)."""
    return math.floor(shares * _SHARE_QUANTUM + _EPS) / _SHARE_QUANTUM


def account_nav(state: AccountState, prices: dict[str, float]) -> float:
    """NAV = cash + Σ shares × price. 보유 종목 가격 결측 시 KeyError (G2 는 호출부)."""
    return state.cash + sum(
        p.shares * prices[s] for s, p in state.positions.items()
    )


def _band_exempt(current_w: float) -> bool:
    """§3.5 범위 밖 드리프트 band 면제 — CHARTER 3~15% 가 band 보다 우선."""
    if current_w > MAX_POSITION_WEIGHT:
        return True
    return 0.0 < current_w < MIN_POSITION_WEIGHT


def compute_trades(
    state: AccountState,
    target_weights: dict[str, float],
    prices: dict[str, float],
    *,
    band: float = DEFAULT_NO_TRADE_BAND,
) -> TradePlan:
    """§3.1 흐름 그대로. target_weights 는 최종(총자산 대비) 비중 (04 부록 A).

    반환 trades 는 매도가 앞, 매수가 뒤 (체결 순서 = 리스트 순서).
    """
    nav = account_nav(state, prices)
    if nav <= 0:
        raise ValueError(f"NAV {nav} ≤ 0 — 평가 불가")

    liquidate_all = not target_weights and bool(state.positions)

    sells: list[TradeOrder] = []
    buys: list[tuple[str, float]] = []  # (symbol, 목표 매수 notional)
    skipped: dict[str, float] = {}

    symbols = sorted(set(state.positions) | set(target_weights))
    for sym in symbols:
        price = prices[sym]
        held = state.positions.get(sym)
        cur_shares = held.shares if held else 0.0
        cur_w = cur_shares * price / nav
        tgt_w = target_weights.get(sym, 0.0)
        delta_w = tgt_w - cur_w

        if abs(delta_w) < band and not _band_exempt(cur_w):
            if abs(delta_w) > _EPS:
                skipped[sym] = abs(delta_w)
            continue

        if delta_w < 0:
            # 매도 — 전량 청산이면 보유 수량 그대로 (절사 잔차 없음)
            if tgt_w <= 0.0:
                shares = cur_shares
                reason = "liquidate_all" if liquidate_all else "exit_position"
            else:
                shares = min(_trunc_shares(-delta_w * nav / price), cur_shares)
                reason = "rebalance"
            if shares <= 0:
                continue
            sells.append(TradeOrder(
                symbol=sym, side="sell", shares=shares, ref_price=price,
                notional=shares * price, reason=reason,
            ))
        elif delta_w > 0:
            buys.append((sym, delta_w * nav))

    # 매도 후 가용 현금 한도 내 매수 — 부족 시 전체 비례 축소 (§3.4)
    cash_available = state.cash + sum(t.notional for t in sells)
    total_buy = sum(n for _, n in buys)
    scale = min(1.0, cash_available / total_buy) if total_buy > 0 else 1.0

    trades = list(sells)
    for sym, notional in buys:
        price = prices[sym]
        shares = _trunc_shares(notional * scale / price)
        if shares <= 0:
            continue
        reason = "new_position" if sym not in state.positions else "rebalance"
        trades.append(TradeOrder(
            symbol=sym, side="buy", shares=shares, ref_price=price,
            notional=shares * price, reason=reason,
        ))
    return TradePlan(trades=trades, skipped_by_band=skipped)


def apply_trades(
    state: AccountState,
    trades: list[TradeOrder],
    as_of_date: date,
) -> AccountState:
    """체결 반영 → 새 AccountState. 무레버리지 불변식 위반 시 ValueError (§3.4).

    avg_cost 는 매수 시 가중 평균, 매도 시 불변 (참고용 필드 — §2.2).
    """
    cash = state.cash
    positions = {s: Position(shares=p.shares, avg_cost=p.avg_cost)
                 for s, p in state.positions.items()}

    for t in trades:
        held = positions.get(t.symbol)
        if t.side == "sell":
            if held is None or held.shares + _EPS < t.shares:
                raise ValueError(
                    f"{t.symbol} 초과 매도: 보유 {held.shares if held else 0} < {t.shares}"
                )
            cash += t.notional
            remaining = round(held.shares - t.shares, SHARE_DECIMALS)
            if remaining <= 0:
                del positions[t.symbol]
            else:
                positions[t.symbol] = Position(
                    shares=remaining, avg_cost=held.avg_cost
                )
        else:
            cash -= t.notional
            if held is None:
                positions[t.symbol] = Position(shares=t.shares, avg_cost=t.ref_price)
            else:
                new_shares = round(held.shares + t.shares, SHARE_DECIMALS)
                new_cost = (held.shares * held.avg_cost + t.notional) / new_shares
                positions[t.symbol] = Position(shares=new_shares, avg_cost=new_cost)

    if cash < -1e-6:
        raise ValueError(f"체결 후 현금 음수 {cash:.6f} — 무레버리지 위반 (§3.4)")
    return AccountState(
        account_id=state.account_id,
        as_of_date=as_of_date,
        cash=max(cash, 0.0),
        positions=positions,
        inception_date=state.inception_date,
    )
