"""context_builder 단위 테스트.

설계 근거: docs/02-bull-bear.md §2.1, §2.1.2

순수 함수 테스트 — 네트워크/S3/FMP 호출 없음. OHLCV pa.Table 픽스처와
FMP-shape dict 픽스처를 직접 주입.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta
from typing import Any

import pyarrow as pa
import pytest

from common.ohlcv import OHLCV_SCHEMA
from screening.schemas import FactorScores, PeerComparable, ScreenedStock

from agents.bull_bear.context_builder import (
    build_context,
    compute_fundamentals_timeseries,
    compute_price_summary,
    to_prompt_markdown,
)
from agents.bull_bear.schemas import (
    FundamentalsTimeseries,
    PriceSummary,
    QuarterlyFigures,
    StockContext,
)

# ---------- OHLCV 픽스처 ----------


def _ohlcv(closes: list[float], start: date = date(2024, 1, 2)) -> pa.Table:
    """주어진 close 시계열로 OHLCV table 생성. 단순 일자 증가 (영업일 가정 X)."""
    n = len(closes)
    dates = [start + timedelta(days=i) for i in range(n)]
    return pa.table(
        {
            "date": dates,
            "open": list(closes),
            "high": list(closes),
            "low": list(closes),
            "close": list(closes),
            "adj_close": list(closes),
            "volume": [1_000_000] * n,
        },
        schema=OHLCV_SCHEMA,
    )


def _empty_ohlcv() -> pa.Table:
    return pa.table(
        {name: [] for name in OHLCV_SCHEMA.names},
        schema=OHLCV_SCHEMA,
    )


# ---------- compute_price_summary ----------


def test_price_summary_empty_table_returns_empty_summary():
    ps = compute_price_summary(_empty_ohlcv(), as_of=date(2026, 5, 4))
    assert ps == PriceSummary()


def test_price_summary_insufficient_data_yields_none_returns():
    closes = [100.0] * 50  # 252+1 미만
    ps = compute_price_summary(_ohlcv(closes), as_of=date(2024, 1, 2) + timedelta(days=49))
    assert ps.return_1y is None
    assert ps.return_6m is None


def test_price_summary_1y_return_exact():
    """간단한 시계열 — 마지막 close 110, 252영업일 전 close 100 → 0.10."""
    n = 253
    closes = [100.0] * n
    closes[-1] = 110.0  # 마지막만 다름
    table = _ohlcv(closes)
    as_of = date(2024, 1, 2) + timedelta(days=n - 1)
    ps = compute_price_summary(table, as_of=as_of)
    assert ps.return_1y == pytest.approx(0.10)


def test_price_summary_6m_return_exact():
    n = 253
    closes = [100.0] * n
    closes[-1] = 105.0
    closes[-1 - 126] = 100.0
    table = _ohlcv(closes)
    as_of = date(2024, 1, 2) + timedelta(days=n - 1)
    ps = compute_price_summary(table, as_of=as_of)
    assert ps.return_6m == pytest.approx(0.05)


def test_price_summary_pct_from_52w_high():
    n = 252
    closes = [100.0] * n
    closes[100] = 200.0  # 1Y 최고가
    closes[-1] = 150.0
    table = _ohlcv(closes)
    as_of = date(2024, 1, 2) + timedelta(days=n - 1)
    ps = compute_price_summary(table, as_of=as_of)
    assert ps.pct_from_52w_high == pytest.approx(150.0 / 200.0 - 1)


def test_price_summary_pct_from_52w_low():
    n = 252
    closes = [100.0] * n
    closes[100] = 50.0  # 1Y 최저가
    closes[-1] = 80.0
    table = _ohlcv(closes)
    as_of = date(2024, 1, 2) + timedelta(days=n - 1)
    ps = compute_price_summary(table, as_of=as_of)
    assert ps.pct_from_52w_low == pytest.approx(80.0 / 50.0 - 1)


def test_price_summary_lookahead_blocked():
    """as_of 이후의 close 가 결과에 영향 없는지 (1Y high 가 미래에 있어도 무시)."""
    n = 300
    closes = [100.0] * n
    closes[280] = 9999.0  # as_of 이후의 spike — 무시되어야 함
    table = _ohlcv(closes)
    as_of = date(2024, 1, 2) + timedelta(days=255)  # 인덱스 255 까지
    ps = compute_price_summary(table, as_of=as_of)
    # 52w high 가 9999 이면 안 됨 — pct_from_52w_high 가 거의 -1 에 가까워지지 않아야
    assert ps.pct_from_52w_high == pytest.approx(0.0)  # 모두 100 이라 high=close=100


def test_price_summary_beta_none_when_spy_missing():
    closes = [100.0 + i * 0.1 for i in range(300)]
    table = _ohlcv(closes)
    as_of = date(2024, 1, 2) + timedelta(days=299)
    ps = compute_price_summary(table, as_of=as_of, spy_ohlcv=None)
    assert ps.beta_1y is None


def _simulate_closes(log_returns: list[float], start_price: float = 100.0) -> list[float]:
    """누적 로그수익률 → 가격 시계열."""
    closes = []
    cum = 0.0
    for r in log_returns:
        cum += r
        closes.append(start_price * math.exp(cum))
    return closes


def test_price_summary_beta_with_correlated_spy():
    """SPY 와 동일한 시계열 → beta=1.0 (자기 자신과의 회귀)."""
    rng = random.Random(42)
    log_returns = [rng.gauss(0, 0.01) for _ in range(260)]
    closes = _simulate_closes(log_returns)
    table = _ohlcv(closes)
    as_of = date(2024, 1, 2) + timedelta(days=len(closes) - 1)
    ps = compute_price_summary(table, as_of=as_of, spy_ohlcv=table)
    assert ps.beta_1y == pytest.approx(1.0, abs=1e-6)


def test_price_summary_beta_with_doubled_spy():
    """종목 = 2 × SPY 변동 → beta ≈ 2.0."""
    rng = random.Random(7)
    spy_log = [rng.gauss(0, 0.01) for _ in range(260)]
    stock_log = [2.0 * r for r in spy_log]
    spy_closes = _simulate_closes(spy_log)
    stock_closes = _simulate_closes(stock_log)
    spy_table = _ohlcv(spy_closes)
    stock_table = _ohlcv(stock_closes)
    as_of = date(2024, 1, 2) + timedelta(days=259)
    ps = compute_price_summary(stock_table, as_of=as_of, spy_ohlcv=spy_table)
    assert ps.beta_1y == pytest.approx(2.0, abs=1e-6)


# ---------- compute_fundamentals_timeseries ----------


def _income_row(d: str, revenue: float | None, eps: float | None) -> dict[str, Any]:
    return {"date": d, "revenue": revenue, "epsdiluted": eps}


def _cashflow_row(d: str, fcf: float | None) -> dict[str, Any]:
    return {"date": d, "freeCashFlow": fcf}


def _quarterly_dates_desc(n: int, start: date = date(2026, 3, 31)) -> list[str]:
    """start 부터 분기씩 거꾸로 n 개의 ISO date 문자열."""
    out = []
    cur = start
    for _ in range(n):
        out.append(cur.isoformat())
        # 분기 = 약 91일
        cur = cur - timedelta(days=91)
    return out


def test_fundamentals_empty_inputs_yield_empty_timeseries():
    fts = compute_fundamentals_timeseries([], [])
    assert fts.quarters == []
    assert fts.revenue_cagr_5y is None
    assert fts.eps_cagr_5y is None
    assert fts.fcf_cagr_5y is None


def test_fundamentals_extracts_top_4_quarters_in_desc_order():
    dates = _quarterly_dates_desc(8)
    income = [_income_row(d, 100.0, 1.0) for d in dates]
    cashflow = [_cashflow_row(d, 50.0) for d in dates]
    fts = compute_fundamentals_timeseries(income, cashflow)
    assert len(fts.quarters) == 4
    # 정렬: 최신 (date desc 첫 항목) 이 quarters[0]
    assert fts.quarters[0].period_end.isoformat() == dates[0]
    assert fts.quarters[3].period_end.isoformat() == dates[3]


def test_fundamentals_unsorted_input_handled():
    """입력이 임의 순서여도 내부에서 정렬."""
    dates = _quarterly_dates_desc(4)
    income = [_income_row(dates[2], 100.0, 1.0), _income_row(dates[0], 200.0, 2.0),
              _income_row(dates[3], 50.0, 0.5), _income_row(dates[1], 150.0, 1.5)]
    fts = compute_fundamentals_timeseries(income, [])
    # 최신이 dates[0] — revenue 200
    assert fts.quarters[0].revenue == 200.0
    assert fts.quarters[0].eps_diluted == 2.0


def test_fundamentals_missing_cashflow_quarter_yields_none_fcf():
    """income 4분기 모두 있고 cashflow 는 일부만 → 빠진 분기는 fcf=None."""
    dates = _quarterly_dates_desc(4)
    income = [_income_row(d, 100.0, 1.0) for d in dates]
    cashflow = [_cashflow_row(dates[0], 50.0), _cashflow_row(dates[2], 30.0)]
    fts = compute_fundamentals_timeseries(income, cashflow)
    assert fts.quarters[0].fcf == 50.0
    assert fts.quarters[1].fcf is None
    assert fts.quarters[2].fcf == 30.0
    assert fts.quarters[3].fcf is None


def test_fundamentals_invalid_date_rows_excluded():
    """date 가 빠지거나 형식이 잘못된 행은 제외 (정상 행만 사용)."""
    valid_dates = _quarterly_dates_desc(4)
    income = [
        {"revenue": 100.0, "epsdiluted": 1.0},  # date 누락
        _income_row("not-a-date", 200.0, 2.0),  # 형식 오류
        _income_row(valid_dates[0], 300.0, 3.0),
        _income_row(valid_dates[1], 250.0, 2.5),
    ]
    fts = compute_fundamentals_timeseries(income, [])
    assert len(fts.quarters) == 2
    assert fts.quarters[0].revenue == 300.0


def test_fundamentals_5y_cagr_computed_with_24_quarters():
    """24분기 입력 — TTM_now=400, TTM_5y=200 → CAGR ≈ (2)^(1/5) - 1 ≈ 0.1487."""
    dates = _quarterly_dates_desc(24)
    # 최신 4분기 합 = 400 (각 100)
    # 5년 전 4분기 합 = 200 (각 50)
    income = []
    for i, d in enumerate(dates):
        if i < 4:
            income.append(_income_row(d, 100.0, 1.0))
        elif 20 <= i < 24:
            income.append(_income_row(d, 50.0, 0.5))
        else:
            income.append(_income_row(d, 75.0, 0.75))
    fts = compute_fundamentals_timeseries(income, [])
    expected = 2.0 ** (1 / 5) - 1
    assert fts.revenue_cagr_5y == pytest.approx(expected)
    assert fts.eps_cagr_5y == pytest.approx(expected)


def test_fundamentals_cagr_none_when_quarters_insufficient():
    """20분기 미만이면 5Y CAGR 산출 불가 (24분기 필요)."""
    dates = _quarterly_dates_desc(20)
    income = [_income_row(d, 100.0, 1.0) for d in dates]
    fts = compute_fundamentals_timeseries(income, [])
    assert fts.revenue_cagr_5y is None


def test_fundamentals_cagr_none_when_ttm_negative():
    """음수 EPS TTM → CAGR 정의상 무의미 → None."""
    dates = _quarterly_dates_desc(24)
    income = []
    for i, d in enumerate(dates):
        # 직전 4분기 EPS 가 모두 음수 → TTM_now < 0
        eps = -1.0 if i < 4 else 1.0
        income.append(_income_row(d, 100.0, eps))
    fts = compute_fundamentals_timeseries(income, [])
    assert fts.eps_cagr_5y is None


def test_fundamentals_cagr_none_when_field_missing_in_window():
    """TTM window 내 결측 필드 1개라도 있으면 CAGR=None (보수적)."""
    dates = _quarterly_dates_desc(24)
    income = [_income_row(d, 100.0, 1.0) for d in dates]
    income[2]["revenue"] = None  # TTM_now window 내 결측
    fts = compute_fundamentals_timeseries(income, [])
    assert fts.revenue_cagr_5y is None
    # EPS 는 결측 없으므로 산출됨
    assert fts.eps_cagr_5y == pytest.approx(0.0)


# ---------- build_context ----------


def _screened_stock() -> ScreenedStock:
    return ScreenedStock(
        symbol="AAPL",
        company_name="Apple Inc.",
        sector="Technology",
        sub_sector="Consumer Electronics",
        rank=1,
        composite_score=1.5,
        factors=FactorScores(momentum_z=1.2, value_z=0.3, pe_ttm=28.0, ev_ebitda=22.0, fcf_yield=0.04),
        peer_context=[PeerComparable(symbol="MSFT", pe_ttm=30.0)],
    )


def test_build_context_integrates_all_sources():
    closes = [100.0] * 253
    closes[-1] = 110.0
    ohlcv = _ohlcv(closes)
    as_of = date(2024, 1, 2) + timedelta(days=252)

    dates = _quarterly_dates_desc(4, start=as_of)
    income = [_income_row(d, 95_000_000_000.0, 1.5) for d in dates]
    cashflow = [_cashflow_row(d, 25_000_000_000.0) for d in dates]

    ctx = build_context(
        _screened_stock(),
        as_of_date=as_of,
        run_id="2024-09-10T00:00:00Z",
        screening_s3_key="screening/dt=2024-09-10/result.json",
        ohlcv=ohlcv,
        income_quarterly=income,
        cashflow_quarterly=cashflow,
    )
    # Identity 평탄화
    assert ctx.symbol == "AAPL"
    assert ctx.composite_score == 1.5
    assert ctx.momentum_z == 1.2
    # Peer context 그대로
    assert ctx.peer_context[0].symbol == "MSFT"
    # Price summary 산출
    assert ctx.price_summary.return_1y == pytest.approx(0.10)
    # Fundamentals 산출
    assert len(ctx.fundamentals.quarters) == 4
    assert ctx.fundamentals.quarters[0].revenue == 95_000_000_000.0
    # Lineage
    assert ctx.run_id == "2024-09-10T00:00:00Z"
    assert ctx.screening_s3_key == "screening/dt=2024-09-10/result.json"


def test_build_context_handles_missing_ohlcv():
    ctx = build_context(
        _screened_stock(),
        as_of_date=date(2026, 5, 4),
        run_id="r1",
        screening_s3_key="k1",
        ohlcv=None,
    )
    assert ctx.price_summary == PriceSummary()
    assert ctx.fundamentals == FundamentalsTimeseries()


# ---------- to_prompt_markdown ----------


def _basic_context(**overrides: object) -> StockContext:
    base: dict[str, object] = {
        "symbol": "AAPL",
        "company_name": "Apple Inc.",
        "sector": "Technology",
        "sub_sector": "Consumer Electronics",
        "as_of_date": date(2026, 5, 4),
        "composite_score": 1.523,
        "momentum_z": 1.2,
        "value_z": -0.3,
        "pe_ttm": 28.0,
        "ev_ebitda": 22.0,
        "fcf_yield": 0.04,
        "peer_context": [],
        "price_summary": PriceSummary(return_1y=0.18, beta_1y=1.1),
        "fundamentals": FundamentalsTimeseries(),
        "run_id": "SECRET-RUN-ID-12345",
        "screening_s3_key": "screening/dt=2026-05-04/result.json",
        "data_quality_flags": ["missing_fcf", "negative_earnings"],
    }
    base.update(overrides)
    return StockContext(**base)  # type: ignore[arg-type]


def test_prompt_markdown_excludes_run_id():
    out = to_prompt_markdown(_basic_context())
    assert "SECRET-RUN-ID-12345" not in out


def test_prompt_markdown_excludes_screening_s3_key():
    out = to_prompt_markdown(_basic_context())
    assert "screening/dt=2026-05-04/result.json" not in out


def test_prompt_markdown_excludes_data_quality_flags():
    out = to_prompt_markdown(_basic_context())
    assert "missing_fcf" not in out
    assert "negative_earnings" not in out


def test_prompt_markdown_includes_identity_and_signals():
    out = to_prompt_markdown(_basic_context())
    assert "AAPL" in out
    assert "Apple Inc." in out
    assert "Technology" in out
    assert "Consumer Electronics" in out
    assert "1.523" in out  # composite_score 3 자리
    assert "+1.20" in out  # momentum_z sign 표기
    assert "-0.30" in out  # value_z sign 표기


def test_prompt_markdown_renders_peer_table_only_when_present():
    no_peers = to_prompt_markdown(_basic_context(peer_context=[]))
    assert "Peer Context" not in no_peers
    with_peers = to_prompt_markdown(
        _basic_context(peer_context=[PeerComparable(symbol="MSFT", pe_ttm=30.0)])
    )
    assert "Peer Context" in with_peers
    assert "MSFT" in with_peers


def test_prompt_markdown_renders_fundamentals_table_only_when_quarters_present():
    no_q = to_prompt_markdown(_basic_context())
    assert "Last 4 Quarters" not in no_q
    fts = FundamentalsTimeseries(
        quarters=[QuarterlyFigures(period_end=date(2026, 3, 31), revenue=95e9, eps_diluted=1.5, fcf=25e9)],
        revenue_cagr_5y=0.08,
    )
    with_q = to_prompt_markdown(_basic_context(fundamentals=fts))
    assert "Last 4 Quarters" in with_q
    assert "$95.00B" in with_q  # 단위 축약
    assert "5Y CAGR" in with_q
    assert "+8.00%" in with_q


def test_prompt_markdown_renders_none_as_n_a():
    ctx = _basic_context(
        momentum_z=None,
        pe_ttm=None,
        fcf_yield=None,
        price_summary=PriceSummary(),  # 모두 None
    )
    out = to_prompt_markdown(ctx)
    # screening signals 와 price summary 의 None 필드는 모두 n/a
    # (정확히 몇 번 등장하는지보다 존재 자체로 검증)
    assert "n/a" in out


def test_prompt_markdown_is_deterministic():
    """동일 입력 → 동일 출력 (회귀 테스트 가능성)."""
    ctx = _basic_context()
    assert to_prompt_markdown(ctx) == to_prompt_markdown(ctx)
