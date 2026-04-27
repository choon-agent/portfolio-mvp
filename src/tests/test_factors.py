"""팩터 raw값 계산 단위 테스트.

순수 함수만 — 네트워크/S3/AWS 호출 없음.
"""
from __future__ import annotations

from datetime import date, timedelta

import pyarrow as pa
import pytest

from common.ohlcv import OHLCV_SCHEMA
from screening.factors import (
    FMP_FIELD_EARNINGS_YIELD_TTM,
    FMP_FIELD_EV_EBITDA_TTM,
    FMP_FIELD_FCF_YIELD_TTM,
    compute_factor_scores,
    compute_momentum,
    extract_value_factors,
)


AS_OF = date(2026, 5, 4)


# ---------- 헬퍼 ----------


def _history(prices: list[float], end_date: date = AS_OF) -> pa.Table:
    """주어진 prices 리스트를 (오래된→최근) 순서로 OHLCV 테이블로.

    end_date 가 마지막 거래일. 그 이전은 캘린더일 단위로 1일씩 거슬러 올라감.
    """
    n = len(prices)
    dates = [end_date - timedelta(days=i) for i in range(n - 1, -1, -1)]
    return pa.table(
        {
            "date": dates,
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "adj_close": prices,
            "volume": [1_000_000] * n,
        },
        schema=OHLCV_SCHEMA,
    )


def _flat_history(n: int, price: float = 100.0) -> pa.Table:
    return _history([price] * n)


# ---------- compute_momentum ----------


def test_momentum_returns_none_when_history_is_none():
    assert compute_momentum(None, AS_OF) == (None, None)


def test_momentum_returns_none_for_empty_table():
    empty = pa.table({n: [] for n in OHLCV_SCHEMA.names}, schema=OHLCV_SCHEMA)
    assert compute_momentum(empty, AS_OF) == (None, None)


def test_momentum_returns_none_when_only_recent_history():
    # 50일치 → 모든 컴포넌트 산출 불가 (skip=21, medium=126, long=252)
    table = _flat_history(50)
    assert compute_momentum(table, AS_OF) == (None, None)


def test_momentum_partial_when_only_6m_computable():
    # medium(126) 은 가능, long(252) 은 불가 → 6m 만 산출
    table = _flat_history(150)  # n=150 → idx 0 == t-149, idx 128 == t-21
    mom_12_1m, mom_6m = compute_momentum(table, AS_OF)
    assert mom_12_1m is None
    assert mom_6m == 0.0  # flat → 수익률 0


def test_momentum_with_known_prices():
    # 253일치 — 정확한 수익률 계산 검증
    # 인덱스 0(t-252) = 100, 인덱스 126(t-126) = 110, 인덱스 231(t-21) = 130
    prices = [100.0] * 253
    prices[126] = 110.0
    prices[231] = 130.0
    table = _history(prices)

    mom_12_1m, mom_6m = compute_momentum(table, AS_OF)
    assert mom_12_1m == pytest.approx(0.30)             # 130/100 - 1
    assert mom_6m == pytest.approx(130 / 110 - 1.0)     # 130/110 - 1


def test_momentum_excludes_rows_after_as_of_date():
    # as_of_date 이후 데이터가 섞여 있어도 무시됨 (실 운영에서는 미래값 누설 방지)
    prices = [100.0] * 253
    prices[126] = 110.0
    prices[231] = 130.0
    table = _history(prices, end_date=AS_OF + timedelta(days=10))
    # 동일 길이 prices 라도 end_date 가 미래면 t-21 위치가 다름 → 다른 결과
    mom_12_1m_with_future, _ = compute_momentum(table, AS_OF + timedelta(days=10))
    mom_12_1m_filtered, _ = compute_momentum(table, AS_OF)
    # 명시적 검증: as_of_date 필터로 다른 결과가 나옴
    assert mom_12_1m_with_future != mom_12_1m_filtered


def test_momentum_handles_zero_or_negative_price_as_missing():
    # 분모가 0 이면 해당 컴포넌트 None
    prices = [100.0] * 253
    prices[0] = 0.0  # t-252
    prices[231] = 130.0
    table = _history(prices)
    mom_12_1m, mom_6m = compute_momentum(table, AS_OF)
    assert mom_12_1m is None
    assert mom_6m is not None


# ---------- extract_value_factors ----------


def test_value_factors_extracts_all_present():
    """P/E 는 earningsYieldTTM 의 역수로 도출."""
    pe, ev, fcf = extract_value_factors(
        {
            FMP_FIELD_EARNINGS_YIELD_TTM: 1.0 / 25.3,  # P/E TTM = 25.3
            FMP_FIELD_EV_EBITDA_TTM: 15.2,
            FMP_FIELD_FCF_YIELD_TTM: 0.04,
        },
    )
    assert pe == pytest.approx(25.3)
    assert ev == 15.2
    assert fcf == 0.04


def test_value_factors_negative_earnings_yield_becomes_none():
    """음수 earnings yield → 음수 P/E → None (적자기업 멀티플 의미 없음)."""
    pe, _, _ = extract_value_factors({FMP_FIELD_EARNINGS_YIELD_TTM: -0.05})
    assert pe is None


def test_value_factors_zero_earnings_yield_becomes_none():
    """0 yield → 1/0 안전하게 None."""
    pe, _, _ = extract_value_factors({FMP_FIELD_EARNINGS_YIELD_TTM: 0.0})
    assert pe is None


def test_value_factors_negative_ev_ebitda_becomes_none():
    _, ev, _ = extract_value_factors({FMP_FIELD_EV_EBITDA_TTM: -5.0})
    assert ev is None


def test_value_factors_negative_fcf_yield_preserved():
    """음의 FCF yield 는 정당한 부정 시그널 — 보존."""
    _, _, fcf = extract_value_factors({FMP_FIELD_FCF_YIELD_TTM: -0.02})
    assert fcf == -0.02


def test_value_factors_handles_none_input():
    assert extract_value_factors(None) == (None, None, None)


def test_value_factors_handles_empty_dict():
    assert extract_value_factors({}) == (None, None, None)


def test_value_factors_handles_non_numeric_values():
    pe, ev, fcf = extract_value_factors(
        {
            FMP_FIELD_EARNINGS_YIELD_TTM: "not-a-number",
            FMP_FIELD_EV_EBITDA_TTM: None,
            FMP_FIELD_FCF_YIELD_TTM: "abc",
        },
    )
    assert pe is None
    assert ev is None
    assert fcf is None


# ---------- compute_factor_scores ----------


def test_compute_factor_scores_composes_momentum_and_value():
    prices = [100.0] * 253
    prices[126] = 110.0
    prices[231] = 130.0
    history = _history(prices)

    scores = compute_factor_scores(
        price_history=history,
        key_metrics_ttm={
            FMP_FIELD_EARNINGS_YIELD_TTM: 1.0 / 20.0,  # P/E TTM = 20
            FMP_FIELD_EV_EBITDA_TTM: 12.0,
            FMP_FIELD_FCF_YIELD_TTM: 0.05,
        },
        as_of_date=AS_OF,
    )

    assert scores.momentum_12_1m == pytest.approx(0.30)
    assert scores.momentum_6m == pytest.approx(130 / 110 - 1.0)
    assert scores.pe_ttm == pytest.approx(20.0)
    assert scores.ev_ebitda == 12.0
    assert scores.fcf_yield == 0.05
    # z-score 는 normalize.py 책임 → 여기서는 None
    assert scores.momentum_z is None
    assert scores.value_z is None


def test_compute_factor_scores_with_all_data_missing():
    scores = compute_factor_scores(
        price_history=None,
        key_metrics_ttm=None,
        as_of_date=AS_OF,
    )
    assert scores.momentum_12_1m is None
    assert scores.momentum_6m is None
    assert scores.pe_ttm is None
    assert scores.ev_ebitda is None
    assert scores.fcf_yield is None
