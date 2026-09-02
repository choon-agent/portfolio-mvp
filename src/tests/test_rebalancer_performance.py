"""rebalancer.performance 단위 테스트 (05 §6)."""
from __future__ import annotations

import math
import statistics

import pytest

from rebalancer.performance import tracking_error, weekly_return


def test_weekly_return_basic() -> None:
    assert weekly_return(10_200.0, 10_000.0) == pytest.approx(0.02)
    assert weekly_return(9_800.0, 10_000.0) == pytest.approx(-0.02)


def test_weekly_return_invalid_prev_nav() -> None:
    with pytest.raises(ValueError, match="NAV"):
        weekly_return(10_000.0, 0.0)


def test_tracking_error_known_values() -> None:
    port = [0.010, -0.005, 0.020, 0.000, 0.015]
    spy = [0.008, -0.001, 0.012, 0.004, 0.010]
    active = [p - b for p, b in zip(port, spy)]
    expected = statistics.stdev(active) * math.sqrt(52)
    assert tracking_error(port, spy) == pytest.approx(expected)


def test_tracking_error_zero_when_identical() -> None:
    r = [0.01, -0.02, 0.005, 0.03]
    assert tracking_error(r, r) == pytest.approx(0.0)


def test_tracking_error_needs_min_samples() -> None:
    # 누적 4주 미만 → None (§6)
    assert tracking_error([0.01, 0.02, 0.03], [0.0, 0.0, 0.0]) is None
    assert tracking_error([0.01] * 4, [0.0] * 4) is not None


def test_tracking_error_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="길이 불일치"):
        tracking_error([0.01] * 5, [0.0] * 4)
