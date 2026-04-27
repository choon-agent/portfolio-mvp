"""스크리닝 2단계 — 팩터 raw값 계산 (종목별).

설계 근거: docs/01-screening.md §3.2

순수 함수 — 네트워크/S3/AWS/LLM 호출 없음.

이 모듈의 책임:
- 종목별 raw 팩터값 산출 (momentum_12_1m, momentum_6m, pe_ttm, ev_ebitda, fcf_yield)
- 결합(0.7×12_1m + 0.3×6m, 밸류 세 컴포넌트 평균)과 섹터 z-score 는 normalize.py 책임

설계상 결정:
- 음수 P/E·EV/EBITDA 는 None 처리 (밸류 평가 의미 없음)
- FCF yield 는 음수도 보존 (음의 FCF 는 정당한 시그널)
- 데이터 부족·결측은 예외 없이 None 반환 (호출 측이 data_quality_flags 로 표시)
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from screening.schemas import FactorScores

# ---------- 기본 lookback (docs/01-screening.md §3.2) ----------

DEFAULT_SKIP_DAYS = 21       # 직전 1개월 제외
DEFAULT_MEDIUM_WINDOW = 126  # 6개월
DEFAULT_LONG_WINDOW = 252    # 12개월


# ---------- 모멘텀 ----------


def compute_momentum(
    price_history: pa.Table | None,
    as_of_date: date,
    *,
    skip_days: int = DEFAULT_SKIP_DAYS,
    medium_window: int = DEFAULT_MEDIUM_WINDOW,
    long_window: int = DEFAULT_LONG_WINDOW,
) -> tuple[float | None, float | None]:
    """직전 1개월을 제외한 모멘텀 두 가지 반환.

    momentum_12_1m = price[t-skip]/price[t-long]  - 1
    momentum_6m    = price[t-skip]/price[t-medium] - 1

    여기서 t = as_of_date, 인덱스는 거래일 단위 (OHLCV row 단위).

    조건 미충족(데이터 부족, 0 이하 가격, 결측) 시 해당 컴포넌트만 None.
    """
    if price_history is None or price_history.num_rows == 0:
        return (None, None)

    dates = price_history.column("date")
    mask = pc.less_equal(dates, pa.scalar(as_of_date, type=pa.date32()))
    eligible = price_history.filter(mask)
    if eligible.num_rows == 0:
        return (None, None)

    sorted_idx = pc.sort_indices(eligible.column("date"))
    prices = eligible.take(sorted_idx).column("adj_close").to_pylist()
    n = len(prices)

    def _price_at(offset: int) -> float | None:
        idx = n - 1 - offset
        if idx < 0:
            return None
        p = prices[idx]
        if p is None:
            return None
        try:
            f = float(p)
        except (TypeError, ValueError):
            return None
        return f if f > 0 else None

    p_skip = _price_at(skip_days)
    p_medium = _price_at(medium_window)
    p_long = _price_at(long_window)

    mom_12_1m = (p_skip / p_long - 1.0) if p_skip is not None and p_long is not None else None
    mom_6m = (p_skip / p_medium - 1.0) if p_skip is not None and p_medium is not None else None
    return (mom_12_1m, mom_6m)


# ---------- 밸류 ----------

# FMP key-metrics-ttm 응답 필드명 (실측 검증 — 2026-04 기준).
# 단일 엔드포인트(key-metrics-ttm) 가 세 필드 모두 제공하므로 ratios-ttm 별도 호출 불필요.
# P/E TTM 은 earningsYieldTTM 의 역수로 도출 (FMP 가 직접 peRatioTTM 을 제공하지 않음).
FMP_FIELD_EARNINGS_YIELD_TTM = "earningsYieldTTM"  # 1/peRatioTTM
FMP_FIELD_EV_EBITDA_TTM = "evToEBITDATTM"
FMP_FIELD_FCF_YIELD_TTM = "freeCashFlowYieldTTM"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive_or_none(value: Any) -> float | None:
    """음수/0/결측을 None 으로 — P/E, EV/EBITDA 처럼 음수면 의미 없는 멀티플용."""
    f = _to_float(value)
    return f if (f is not None and f > 0) else None


def extract_value_factors(
    key_metrics_ttm: dict[str, Any] | None,
) -> tuple[float | None, float | None, float | None]:
    """FMP key-metrics-ttm 응답에서 (pe_ttm, ev_ebitda, fcf_yield) 추출.

    P/E TTM 은 earningsYieldTTM 의 역수로 도출 (양수 yield 일 때만):
        peRatioTTM = 1 / earningsYieldTTM
    음수/0 yield 는 None — '음수 P/E 는 의미 없음' 정책 (§3.2) 과 정합.

    EV/EBITDA: 양수만 유효 (음수는 None — 적자 EBITDA 시 멀티플 의미 없음)
    FCF yield: 음수도 보존 (음의 FCF 는 정당한 부정 시그널)

    응답 dict 가 None 이거나 키가 없으면 해당 컴포넌트만 None.
    """
    if not key_metrics_ttm:
        return (None, None, None)

    earnings_yield = _to_float(key_metrics_ttm.get(FMP_FIELD_EARNINGS_YIELD_TTM))
    pe = (
        1.0 / earnings_yield
        if earnings_yield is not None and earnings_yield > 0
        else None
    )
    ev_ebitda = _positive_or_none(key_metrics_ttm.get(FMP_FIELD_EV_EBITDA_TTM))
    fcf_y = _to_float(key_metrics_ttm.get(FMP_FIELD_FCF_YIELD_TTM))
    return (pe, ev_ebitda, fcf_y)


# ---------- 합성 ----------


def compute_factor_scores(
    price_history: pa.Table | None,
    key_metrics_ttm: dict[str, Any] | None,
    as_of_date: date,
) -> FactorScores:
    """단일 종목의 raw FactorScores 반환.

    입력은 OHLCV 와 FMP key-metrics-ttm 응답만 — 단일 엔드포인트로 모든 밸류 컴포넌트 산출.
    z-score 필드(momentum_z, value_z) 는 None 으로 둠 — normalize.py 가 단면 단위로 채움.
    """
    mom_12_1m, mom_6m = compute_momentum(price_history, as_of_date)
    pe, ev_ebitda, fcf_y = extract_value_factors(key_metrics_ttm)
    return FactorScores(
        momentum_12_1m=mom_12_1m,
        momentum_6m=mom_6m,
        pe_ttm=pe,
        ev_ebitda=ev_ebitda,
        fcf_yield=fcf_y,
    )
