"""스크리닝 1단계 — 유니버스 필터.

설계 근거: docs/01-screening.md §3.1

순수 함수 — 네트워크/S3/AWS/LLM 호출 없음.

각 컷을 독립 predicate 로 분리해 단위 테스트와 조합 가능성 확보.
filter_universe() 가 모든 컷을 정해진 순서로 적용하고, 통과 종목과
드롭 사유를 함께 반환 (관측·디버깅용).
"""
from __future__ import annotations

from datetime import date, timedelta

import pyarrow as pa
import pyarrow.compute as pc
from pydantic import BaseModel, Field

from common.models import Constituent

# ---------- 기본 임계값 (docs/01-screening.md §3.1) ----------

DEFAULT_MIN_MARKET_CAP = 2_000_000_000  # $2B
DEFAULT_MIN_AVG_DOLLAR_VOLUME = 20_000_000  # $20M
DEFAULT_DOLLAR_VOLUME_LOOKBACK_DAYS = 60  # 직전 60영업일
DEFAULT_MIN_PRICE_HISTORY_DAYS = 250  # 직전 12개월 거래일 수
DEFAULT_PRICE_HISTORY_WINDOW_DAYS = 365  # 12개월 캘린더 윈도우
DEFAULT_MIN_MEMBERSHIP_DAYS = 365  # 신규 편입 1년 미만 제외


# ---------- per-stock predicates ----------


def is_current_member(constituent: Constituent) -> bool:
    """현재 S&P 500 구성원 여부."""
    return constituent.is_current


def is_seasoned(
    constituent: Constituent,
    as_of_date: date,
    min_membership_days: int = DEFAULT_MIN_MEMBERSHIP_DAYS,
) -> bool:
    """편입 후 min_membership_days 이상 경과했는지.

    이유: 신규 편입 직후는 가격·재무 데이터가 짧아 모멘텀/밸류 z-score 신뢰도 낮음.
    """
    return (as_of_date - constituent.date_added).days >= min_membership_days


def has_sufficient_market_cap(
    market_cap: float | None,
    threshold: float = DEFAULT_MIN_MARKET_CAP,
) -> bool:
    """시총 임계 통과. None(데이터 결측)은 실패로 처리."""
    return market_cap is not None and market_cap >= threshold


def has_sufficient_history(
    price_history: pa.Table | None,
    as_of_date: date,
    min_days: int = DEFAULT_MIN_PRICE_HISTORY_DAYS,
    window_days: int = DEFAULT_PRICE_HISTORY_WINDOW_DAYS,
) -> bool:
    """as_of_date 기준 직전 window_days 캘린더 윈도우 안에
    min_days 이상의 거래일 데이터가 있는지.

    OHLCV 는 거래일만 가지므로 윈도우 내 row 수가 곧 거래일 수.
    """
    if price_history is None or price_history.num_rows == 0:
        return False
    window_start = as_of_date - timedelta(days=window_days)
    dates = price_history.column("date")
    mask = pc.and_(
        pc.greater(dates, pa.scalar(window_start, type=pa.date32())),
        pc.less_equal(dates, pa.scalar(as_of_date, type=pa.date32())),
    )
    in_window = pc.sum(mask).as_py() or 0
    return in_window >= min_days


def has_sufficient_dollar_volume(
    price_history: pa.Table | None,
    as_of_date: date,
    threshold: float = DEFAULT_MIN_AVG_DOLLAR_VOLUME,
    lookback_days: int = DEFAULT_DOLLAR_VOLUME_LOOKBACK_DAYS,
) -> bool:
    """as_of_date 이전 lookback_days 거래일의 평균 거래대금이 threshold 이상인지.

    거래대금 = close × volume (간단형 — VWAP 근사).
    표본이 lookback_days 미만이면 False (보통 has_sufficient_history 가 먼저 잡음).
    """
    if price_history is None or price_history.num_rows == 0:
        return False
    dates = price_history.column("date")
    mask = pc.less_equal(dates, pa.scalar(as_of_date, type=pa.date32()))
    eligible = price_history.filter(mask)
    if eligible.num_rows < lookback_days:
        return False

    sorted_idx = pc.sort_indices(eligible.column("date"))
    eligible = eligible.take(sorted_idx)
    recent = eligible.slice(eligible.num_rows - lookback_days, lookback_days)

    close = recent.column("close")
    volume = pc.cast(recent.column("volume"), pa.float64())
    avg = pc.mean(pc.multiply(close, volume)).as_py()
    return avg is not None and avg >= threshold


# ---------- 결과 타입 ----------


class UniverseFilterResult(BaseModel):
    """필터 결과.

    passed: 다음 단계(factors.py)로 흐를 종목.
    dropped_reasons: 드롭된 종목의 사유 (관측·디버깅용).
                     키는 symbol, 값은 §3.1 컷 이름.
    """

    passed: list[Constituent] = Field(default_factory=list)
    dropped_reasons: dict[str, str] = Field(default_factory=dict)


# ---------- composition ----------

# 드롭 사유 코드 (관측 로그·테스트에서 참조)
REASON_REMOVED_MEMBER = "removed_member"
REASON_NEW_MEMBER = "new_member_under_year"
REASON_LOW_MARKET_CAP = "market_cap_below_threshold"
REASON_INSUFFICIENT_HISTORY = "insufficient_price_history"
REASON_LOW_DOLLAR_VOLUME = "low_dollar_volume"


def filter_universe(
    constituents: list[Constituent],
    market_caps: dict[str, float | None],
    price_histories: dict[str, pa.Table | None],
    as_of_date: date,
    *,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP,
    min_avg_dollar_volume: float = DEFAULT_MIN_AVG_DOLLAR_VOLUME,
    dollar_volume_lookback_days: int = DEFAULT_DOLLAR_VOLUME_LOOKBACK_DAYS,
    min_price_history_days: int = DEFAULT_MIN_PRICE_HISTORY_DAYS,
    price_history_window_days: int = DEFAULT_PRICE_HISTORY_WINDOW_DAYS,
    min_membership_days: int = DEFAULT_MIN_MEMBERSHIP_DAYS,
) -> UniverseFilterResult:
    """모든 컷을 정해진 순서로 적용.

    적용 순서 (조기 컷 → 비싼 컷):
      1. 현재 구성원
      2. seasoning (편입 1년 이상)
      3. 시총
      4. 가격 이력 충분성 (12개월 윈도우 내 250영업일)
      5. 거래대금 (직전 60영업일 평균)

    반환된 dropped_reasons 의 사유 문자열은 REASON_* 상수 참조.
    """
    result = UniverseFilterResult()

    for c in constituents:
        if not is_current_member(c):
            result.dropped_reasons[c.symbol] = REASON_REMOVED_MEMBER
            continue
        if not is_seasoned(c, as_of_date, min_membership_days):
            result.dropped_reasons[c.symbol] = REASON_NEW_MEMBER
            continue
        if not has_sufficient_market_cap(market_caps.get(c.symbol), min_market_cap):
            result.dropped_reasons[c.symbol] = REASON_LOW_MARKET_CAP
            continue

        history = price_histories.get(c.symbol)
        if not has_sufficient_history(
            history,
            as_of_date,
            min_price_history_days,
            price_history_window_days,
        ):
            result.dropped_reasons[c.symbol] = REASON_INSUFFICIENT_HISTORY
            continue
        if not has_sufficient_dollar_volume(
            history,
            as_of_date,
            min_avg_dollar_volume,
            dollar_volume_lookback_days,
        ):
            result.dropped_reasons[c.symbol] = REASON_LOW_DOLLAR_VOLUME
            continue

        result.passed.append(c)

    return result
