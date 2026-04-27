"""유니버스 필터 단위 테스트.

각 컷 predicate 를 독립 테스트 + filter_universe 조합 동작 검증.
네트워크/S3/AWS 호출 없음 — pa.Table 픽스처로만 작동.
"""
from __future__ import annotations

from datetime import date, timedelta

import pyarrow as pa

from common.models import Constituent
from common.ohlcv import OHLCV_SCHEMA
from screening.universe import (
    REASON_INSUFFICIENT_HISTORY,
    REASON_LOW_DOLLAR_VOLUME,
    REASON_LOW_MARKET_CAP,
    REASON_NEW_MEMBER,
    REASON_REMOVED_MEMBER,
    filter_universe,
    has_sufficient_dollar_volume,
    has_sufficient_history,
    has_sufficient_market_cap,
    is_current_member,
    is_seasoned,
)


# ---------- 헬퍼 ----------


AS_OF = date(2026, 5, 4)


def _history(
    end_date: date = AS_OF,
    days: int = 300,
    *,
    close: float = 100.0,
    volume: int = 500_000,
) -> pa.Table:
    """end_date 까지 직전 days 영업일치 OHLCV 테이블.

    캘린더일을 영업일로 단순화 (실제 OHLCV 도 휴일 행은 없음 — 윈도우 내 row 수만 유의).
    """
    dates = [end_date - timedelta(days=i) for i in range(days - 1, -1, -1)]
    n = len(dates)
    return pa.table(
        {
            "date": dates,
            "open": [close] * n,
            "high": [close] * n,
            "low": [close] * n,
            "close": [close] * n,
            "adj_close": [close] * n,
            "volume": [volume] * n,
        },
        schema=OHLCV_SCHEMA,
    )


def _constituent(
    symbol: str,
    *,
    date_added: date = date(2010, 1, 1),
    date_removed: date | None = None,
) -> Constituent:
    return Constituent(
        symbol=symbol,
        company_name=f"{symbol} Inc.",
        sector="Technology",
        sub_sector="Software",
        date_added=date_added,
        date_removed=date_removed,
    )


# ---------- is_current_member / is_seasoned ----------


def test_is_current_member_true_for_active_holding():
    assert is_current_member(_constituent("AAPL")) is True


def test_is_current_member_false_for_removed_holding():
    assert is_current_member(_constituent("LEH", date_removed=date(2008, 9, 22))) is False


def test_is_seasoned_passes_for_long_term_member():
    assert is_seasoned(_constituent("AAPL", date_added=date(2010, 1, 1)), AS_OF) is True


def test_is_seasoned_rejects_recent_addition():
    recent = _constituent("NEW", date_added=AS_OF - timedelta(days=180))
    assert is_seasoned(recent, AS_OF) is False


def test_is_seasoned_at_threshold_boundary():
    boundary = _constituent("BDR", date_added=AS_OF - timedelta(days=365))
    assert is_seasoned(boundary, AS_OF) is True


# ---------- has_sufficient_market_cap ----------


def test_market_cap_passes_at_threshold():
    assert has_sufficient_market_cap(2_000_000_000) is True


def test_market_cap_passes_above_threshold():
    assert has_sufficient_market_cap(5_000_000_000) is True


def test_market_cap_rejects_below_threshold():
    assert has_sufficient_market_cap(1_500_000_000) is False


def test_market_cap_rejects_none():
    assert has_sufficient_market_cap(None) is False


# ---------- has_sufficient_history ----------


def test_history_passes_with_full_year_of_trading_days():
    table = _history(end_date=AS_OF, days=260)
    assert has_sufficient_history(table, AS_OF) is True


def test_history_rejects_when_window_count_under_minimum():
    # 100일치 — 250 미만
    table = _history(end_date=AS_OF, days=100)
    assert has_sufficient_history(table, AS_OF) is False


def test_history_rejects_none():
    assert has_sufficient_history(None, AS_OF) is False


def test_history_rejects_empty_table():
    empty = pa.table(
        {name: [] for name in OHLCV_SCHEMA.names},
        schema=OHLCV_SCHEMA,
    )
    assert has_sufficient_history(empty, AS_OF) is False


def test_history_excludes_rows_outside_window():
    # 윈도우 밖 (2년 전) 1000일치 + 윈도우 안 100일치 → 100일만 카운트되어 실패
    old = _history(end_date=AS_OF - timedelta(days=400), days=1000)
    recent = _history(end_date=AS_OF, days=100)
    combined = pa.concat_tables([old, recent])
    assert has_sufficient_history(combined, AS_OF) is False


# ---------- has_sufficient_dollar_volume ----------


def test_dollar_volume_passes_with_high_volume():
    # close=$100, volume=300_000 → daily $30M
    table = _history(end_date=AS_OF, days=80, close=100.0, volume=300_000)
    assert has_sufficient_dollar_volume(table, AS_OF) is True


def test_dollar_volume_rejects_low_volume():
    # close=$100, volume=100_000 → daily $10M (threshold $20M)
    table = _history(end_date=AS_OF, days=80, close=100.0, volume=100_000)
    assert has_sufficient_dollar_volume(table, AS_OF) is False


def test_dollar_volume_rejects_short_history():
    # 50일치만 — lookback 60 미만 → False
    table = _history(end_date=AS_OF, days=50, close=100.0, volume=10_000_000)
    assert has_sufficient_dollar_volume(table, AS_OF) is False


def test_dollar_volume_uses_only_recent_lookback():
    # 과거에만 거래대금이 컸고 최근 60일은 낮으면 → 실패
    old_high = _history(end_date=AS_OF - timedelta(days=200), days=200, close=100.0, volume=10_000_000)
    recent_low = _history(end_date=AS_OF, days=80, close=100.0, volume=50_000)
    combined = pa.concat_tables([old_high, recent_low])
    assert has_sufficient_dollar_volume(combined, AS_OF) is False


# ---------- filter_universe ----------


def _good_history() -> pa.Table:
    return _history(end_date=AS_OF, days=300, close=100.0, volume=300_000)


def test_filter_universe_passes_qualifying_stock():
    c = _constituent("AAPL")
    result = filter_universe(
        [c],
        market_caps={"AAPL": 3_000_000_000_000},
        price_histories={"AAPL": _good_history()},
        as_of_date=AS_OF,
    )
    assert [s.symbol for s in result.passed] == ["AAPL"]
    assert result.dropped_reasons == {}


def test_filter_universe_records_each_drop_reason():
    constituents = [
        _constituent("REMOVED", date_removed=date(2020, 1, 1)),
        _constituent("NEW", date_added=AS_OF - timedelta(days=100)),
        _constituent("SMALL"),
        _constituent("SHORTHIST"),
        _constituent("ILLIQUID"),
        _constituent("OK"),
    ]
    result = filter_universe(
        constituents,
        market_caps={
            "REMOVED": 5_000_000_000,
            "NEW": 5_000_000_000,
            "SMALL": 500_000_000,            # < $2B
            "SHORTHIST": 5_000_000_000,
            "ILLIQUID": 5_000_000_000,
            "OK": 5_000_000_000,
        },
        price_histories={
            "REMOVED": _good_history(),
            "NEW": _good_history(),
            "SMALL": _good_history(),
            "SHORTHIST": _history(end_date=AS_OF, days=100),  # < 250
            "ILLIQUID": _history(end_date=AS_OF, days=300, close=100.0, volume=50_000),  # daily $5M
            "OK": _good_history(),
        },
        as_of_date=AS_OF,
    )

    assert [s.symbol for s in result.passed] == ["OK"]
    assert result.dropped_reasons == {
        "REMOVED": REASON_REMOVED_MEMBER,
        "NEW": REASON_NEW_MEMBER,
        "SMALL": REASON_LOW_MARKET_CAP,
        "SHORTHIST": REASON_INSUFFICIENT_HISTORY,
        "ILLIQUID": REASON_LOW_DOLLAR_VOLUME,
    }


def test_filter_universe_handles_missing_market_cap_data():
    c = _constituent("NODATA")
    result = filter_universe(
        [c],
        market_caps={},  # 키 없음 → None 취급
        price_histories={"NODATA": _good_history()},
        as_of_date=AS_OF,
    )
    assert result.passed == []
    assert result.dropped_reasons == {"NODATA": REASON_LOW_MARKET_CAP}


def test_filter_universe_handles_missing_price_history():
    c = _constituent("NOPRICE")
    result = filter_universe(
        [c],
        market_caps={"NOPRICE": 5_000_000_000},
        price_histories={},
        as_of_date=AS_OF,
    )
    assert result.passed == []
    assert result.dropped_reasons == {"NOPRICE": REASON_INSUFFICIENT_HISTORY}
