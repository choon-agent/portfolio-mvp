"""5단계 리밸런싱 — 계좌·매매 스키마.

설계 근거: docs/05-rebalancing.md §2.2 (v0.2 확정)

- `TradeOrder` — 주간 매매 주문 1건. 체결가는 직전 거래일 adj_close 기록 (§3.2).
- `AccountState` — 페이퍼 계좌의 현재 상태 (`accounts/{id}/state.json`).
  초기값 $10,000 (CHARTER §2.3). primary / option_b 두 계좌 병렬 (§3.6).
- `RebalanceSnapshot` — 주차별 불변 기록 (`accounts/{id}/dt=D/snapshot.json`).
  pre/post 상태 + 매매 + 성과 + lineage. state.json 유실 시 복원 원본 (§2.2 쓰기 순서).

매매 결정 LLM 사용 없음 (CHARTER §3.3) — 모든 필드가 결정적 산출.
순수 데이터 — 네트워크/S3 호출 없음.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "ACCOUNT_IDS",
    "DEFAULT_INITIAL_CASH",
    "DEFAULT_NO_TRADE_BAND",
    "SHARE_DECIMALS",
    "AccountId",
    "TradeOrder",
    "Position",
    "AccountState",
    "RebalanceSnapshot",
]

AccountId = Literal["primary", "option_b"]
ACCOUNT_IDS: tuple[AccountId, ...] = ("primary", "option_b")

DEFAULT_INITIAL_CASH = 10_000.0   # CHARTER §2.3 페이퍼 계좌
DEFAULT_NO_TRADE_BAND = 0.015     # §3.5 확정 — 환경변수 REBALANCE_BAND 로 조정
SHARE_DECIMALS = 4                # §3.3 소수점 주식 (소수 4자리 절사)

_NOTIONAL_TOL = 0.01              # notional = shares × ref_price 정합 허용 오차 ($)


class TradeOrder(BaseModel):
    """매매 주문 1건 (§2.2). 체결 완료로 간주된 기록 — 페이퍼 즉시 체결 (§3.2)."""

    symbol: str = Field(min_length=1)
    side: Literal["buy", "sell"]
    shares: float = Field(gt=0.0)            # 소수 4자리 (validator)
    ref_price: float = Field(gt=0.0)         # 직전 거래일 adj_close
    notional: float = Field(gt=0.0)          # shares × ref_price
    reason: Literal["rebalance", "new_position", "exit_position", "liquidate_all"]

    @model_validator(mode="after")
    def _validate_order(self) -> Self:
        if round(self.shares, SHARE_DECIMALS) != self.shares:
            raise ValueError(
                f"{self.symbol} shares {self.shares} — 소수 {SHARE_DECIMALS}자리 초과 (§3.3)"
            )
        if abs(self.notional - self.shares * self.ref_price) > _NOTIONAL_TOL:
            raise ValueError(
                f"{self.symbol} notional {self.notional:.4f} ≠ "
                f"shares×ref_price {self.shares * self.ref_price:.4f}"
            )
        # reason ↔ side 정합 (§3.1): 편입은 매수, 청산은 매도
        if self.reason == "new_position" and self.side != "buy":
            raise ValueError(f"{self.symbol} new_position 은 buy 여야 함")
        if self.reason in ("exit_position", "liquidate_all") and self.side != "sell":
            raise ValueError(f"{self.symbol} {self.reason} 은 sell 이어야 함")
        return self


class Position(BaseModel):
    """보유 포지션. shares=0 포지션은 dict 에서 제거 (미보유 표현은 부재)."""

    shares: float = Field(gt=0.0)
    avg_cost: float = Field(gt=0.0)          # 평균 취득 단가 (참고용 — 매매 규칙 미사용)


class AccountState(BaseModel):
    """페이퍼 계좌 현재 상태 (`accounts/{id}/state.json`)."""

    account_id: AccountId
    as_of_date: date                         # 마지막 리밸런싱 기준일
    cash: float = Field(ge=0.0)              # 무레버리지 불변식 (§3.4)
    positions: dict[str, Position] = Field(default_factory=dict)
    inception_date: date                     # 계좌 생성일 (성과 기산점 — §4.1)

    @model_validator(mode="after")
    def _validate_dates(self) -> Self:
        if self.as_of_date < self.inception_date:
            raise ValueError(
                f"as_of_date {self.as_of_date} < inception_date {self.inception_date}"
            )
        return self


class RebalanceSnapshot(BaseModel):
    """주차별 불변 기록 (`accounts/{id}/dt=D/snapshot.json` — §2.2).

    쓰기 순서: snapshot 먼저 → state.json 갱신 (08-17 오염 사고 교훈).
    이미 존재하는 dt 파티션에는 재기록 금지 — 멱등 가드 (§4.3).
    """

    account_id: AccountId
    as_of_date: date
    pre_state: AccountState                  # 체결 전 (원복·감사용)
    trades: list[TradeOrder] = Field(default_factory=list)
    post_state: AccountState
    # ---- 평가·성과 (§6) ----
    nav: float = Field(ge=0.0)               # 체결 후 NAV = cash + Σ shares×price
    prices_used: dict[str, float] = Field(default_factory=dict)  # 평가가 lineage
    weekly_return: float | None = None       # 직전 스냅샷 대비 (첫 주 None)
    spy_weekly_return: float | None = None
    # ---- lineage ----
    target_dt: date | None = None            # 소비한 target.json 파티션 (보유 유지 주 None)
    no_trade_band: float = Field(ge=0.0, le=1.0)
    skipped_by_band: dict[str, float] = Field(default_factory=dict)  # symbol → |Δw|
    computed_at: datetime

    @model_validator(mode="after")
    def _validate_snapshot(self) -> Self:
        if not (self.pre_state.account_id == self.post_state.account_id == self.account_id):
            raise ValueError(
                f"account_id 불일치: snapshot={self.account_id} "
                f"pre={self.pre_state.account_id} post={self.post_state.account_id}"
            )
        if self.post_state.as_of_date != self.as_of_date:
            raise ValueError(
                f"post_state.as_of_date {self.post_state.as_of_date} ≠ "
                f"snapshot as_of_date {self.as_of_date}"
            )
        # NAV 정합: post 상태를 prices_used 로 평가한 값과 일치 (§6 — 동일 가격 원칙)
        missing = [s for s in self.post_state.positions if s not in self.prices_used]
        if missing:
            raise ValueError(f"prices_used 에 보유 종목 가격 결측: {missing}")
        recomputed = self.post_state.cash + sum(
            p.shares * self.prices_used[s] for s, p in self.post_state.positions.items()
        )
        if abs(recomputed - self.nav) > 0.01:
            raise ValueError(f"nav {self.nav:.4f} ≠ 재계산 {recomputed:.4f}")
        return self
