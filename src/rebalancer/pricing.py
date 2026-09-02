"""가격 로드 + 게이트 G2/G4 (05 §2.1, §5) — I/O 격리 계층.

체결·평가 가격 = OHLCV parquet 의 `as_of 이하 마지막 adj_close` (§3.2).
`as_of` 파라미터로 과거 주차 리플레이(백필 — §4.1)도 같은 코드 경로.

게이트:
  G2 가격 결측 — 보유·target 종목의 OHLCV 없음/기간 내 행 없음 → 런 실패
  G4 신선도   — 마지막 가격이 as_of 대비 STALE_CALENDAR_DAYS 초과 과거 → 런 실패
                (5 거래일 ≈ 달력 7일 근사. update_ohlcv 상류 문제 신호)

LLM 사용 없음. SPY(벤치마크)는 결측 허용 — §6 은 None 으로 성과만 미기록.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from common.s3_io import read_parquet

__all__ = [
    "STALE_CALENDAR_DAYS",
    "PriceMissingError",
    "StalePriceError",
    "load_prices",
    "load_price_optional",
]

STALE_CALENDAR_DAYS = 7   # ≈ 5 거래일 (§5 G4)


class PriceMissingError(RuntimeError):
    """G2 — 필수 종목 가격 결측 (부분 체결은 상태 오염 → 런 실패)."""


class StalePriceError(RuntimeError):
    """G4 — 가격이 오래됨 (update_ohlcv 상류 문제 신호 → 런 실패)."""


def _last_price_asof(bucket: str, symbol: str, as_of: date) -> tuple[float, date] | None:
    table = read_parquet(bucket, f"ohlcv/ticker={symbol}/data.parquet")
    if table is None:
        return None
    df = table.to_pandas()
    dates = pd.to_datetime(df["date"]).dt.date
    mask = dates <= as_of
    if not mask.any():
        return None
    idx = dates[mask].index[-1]
    return float(df["adj_close"].loc[idx]), dates.loc[idx]


def load_prices(bucket: str, symbols: list[str], as_of: date) -> dict[str, float]:
    """필수 종목(보유 ∪ target) 가격 — G2/G4 위반 시 예외 (조용한 스킵 금지)."""
    prices: dict[str, float] = {}
    missing: list[str] = []
    for sym in sorted(set(symbols)):
        found = _last_price_asof(bucket, sym, as_of)
        if found is None:
            missing.append(sym)
            continue
        price, price_date = found
        if (as_of - price_date).days > STALE_CALENDAR_DAYS:            # G4
            raise StalePriceError(
                f"{sym} 마지막 가격 {price_date} — as_of {as_of} 대비 "
                f"{(as_of - price_date).days}일 경과 (한도 {STALE_CALENDAR_DAYS})"
            )
        prices[sym] = price
    if missing:                                                        # G2
        raise PriceMissingError(f"가격 결측: {missing}")
    return prices


def load_price_optional(bucket: str, symbol: str, as_of: date) -> float | None:
    """벤치마크(SPY) 등 결측 허용 가격 — 없으면 None (신선도 검사는 동일 적용)."""
    found = _last_price_asof(bucket, symbol, as_of)
    if found is None:
        return None
    price, price_date = found
    if (as_of - price_date).days > STALE_CALENDAR_DAYS:
        return None
    return price
