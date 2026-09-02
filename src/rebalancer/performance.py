"""성과 측정 — 순수 함수 (05 §6, v0.2 확정).

CHARTER §4.1 실전 전환 기준의 조작적 정의:
- 주간 수익률 r_t = NAV_t / NAV_{t-1} − 1 (연속 스냅샷 간, 동일 평가 규칙)
- tracking error = std(r_t − r_SPY,t) × √52 ≤ 0.15 (주간 액티브 수익률의
  연환산 표준편차 — M3 말 Charter 재검토 시 정의 추인)

numpy 불필요 — statistics 표준 라이브러리만. LLM/S3 호출 없음.
"""
from __future__ import annotations

import math
import statistics

__all__ = [
    "MIN_TE_SAMPLES",
    "WEEKS_PER_YEAR",
    "weekly_return",
    "tracking_error",
]

WEEKS_PER_YEAR = 52
MIN_TE_SAMPLES = 4   # §6 — 누적 4주부터 기록 (그 미만은 통계 무의미 → None)


def weekly_return(nav: float, prev_nav: float) -> float:
    """r_t = NAV_t / NAV_{t-1} − 1. 직전 NAV ≤ 0 이면 ValueError (계좌 오염 신호)."""
    if prev_nav <= 0:
        raise ValueError(f"직전 NAV {prev_nav} ≤ 0 — 수익률 정의 불가")
    return nav / prev_nav - 1.0


def tracking_error(
    portfolio_returns: list[float],
    benchmark_returns: list[float],
) -> float | None:
    """연환산 TE = stdev(주간 액티브 수익률) × √52 (표본 표준편차, ddof=1).

    두 시계열은 같은 주차 순서로 정렬된 쌍 — 길이 불일치는 호출부 버그 (ValueError).
    표본 < MIN_TE_SAMPLES 면 None (§6 — 판정 불가·미기록).
    """
    if len(portfolio_returns) != len(benchmark_returns):
        raise ValueError(
            f"수익률 길이 불일치: portfolio {len(portfolio_returns)} "
            f"≠ benchmark {len(benchmark_returns)}"
        )
    if len(portfolio_returns) < MIN_TE_SAMPLES:
        return None
    active = [r - b for r, b in zip(portfolio_returns, benchmark_returns)]
    return statistics.stdev(active) * math.sqrt(WEEKS_PER_YEAR)
