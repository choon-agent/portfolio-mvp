"""key-metrics-ttm cache-aside 단위 테스트.

순수 로직(load_or_fetch_pure, is_cache_fresh)만 테스트 — S3·FMP 의존 없음.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from common.fmp_client import FMPError
from common.fundamentals import (
    is_cache_fresh,
    load_or_fetch_pure,
)


NOW = datetime(2026, 4, 27, 10, 0, 0, tzinfo=timezone.utc)


# ---------- is_cache_fresh ----------


def test_cache_fresh_when_within_age():
    entry = {"cached_at": (NOW - timedelta(days=10)).isoformat(), "data": {}}
    assert is_cache_fresh(entry, max_age_days=90, now=NOW) is True


def test_cache_stale_when_beyond_age():
    entry = {"cached_at": (NOW - timedelta(days=100)).isoformat(), "data": {}}
    assert is_cache_fresh(entry, max_age_days=90, now=NOW) is False


def test_cache_fresh_at_exact_boundary():
    entry = {"cached_at": (NOW - timedelta(days=90)).isoformat(), "data": {}}
    assert is_cache_fresh(entry, max_age_days=90, now=NOW) is True


def test_cache_fresh_returns_false_for_none():
    assert is_cache_fresh(None, max_age_days=90, now=NOW) is False


def test_cache_fresh_returns_false_for_missing_timestamp():
    assert is_cache_fresh({"data": {}}, max_age_days=90, now=NOW) is False


def test_cache_fresh_returns_false_for_unparseable_timestamp():
    assert is_cache_fresh({"cached_at": "not-iso", "data": {}}, 90, now=NOW) is False


def test_cache_fresh_handles_naive_timestamp_as_utc():
    """tz 없는 ISO 도 UTC 로 가정해 fresh 판정 성공."""
    entry = {"cached_at": (NOW - timedelta(days=5)).replace(tzinfo=None).isoformat(), "data": {}}
    assert is_cache_fresh(entry, 90, now=NOW) is True


# ---------- load_or_fetch_pure ----------


class _Recorder:
    """callable 호출 기록용."""

    def __init__(self, value: Any = None, *, raises: Exception | None = None):
        self.value = value
        self.raises = raises
        self.calls = 0
        self.last_payload: Any = None

    def __call__(self, *args):
        self.calls += 1
        if args:
            self.last_payload = args[0]
        if self.raises:
            raise self.raises
        return self.value


def test_returns_cached_data_on_fresh_hit():
    cached = {"cached_at": (NOW - timedelta(days=10)).isoformat(), "data": {"marketCap": 100}}
    cache_read = _Recorder(value=cached)
    cache_write = _Recorder()
    fmp_call = _Recorder()  # 호출되면 실패

    result = load_or_fetch_pure(
        cache_read=cache_read,
        cache_write=cache_write,
        fmp_call=fmp_call,
        max_age_days=90,
        now=NOW,
    )

    assert result == {"marketCap": 100}
    assert fmp_call.calls == 0  # FMP 호출 없음
    assert cache_write.calls == 0  # 쓰기 없음


def test_calls_fmp_on_cache_miss():
    cache_read = _Recorder(value=None)
    cache_write = _Recorder()
    fmp_call = _Recorder(value={"marketCap": 200})

    result = load_or_fetch_pure(
        cache_read=cache_read,
        cache_write=cache_write,
        fmp_call=fmp_call,
        now=NOW,
    )

    assert result == {"marketCap": 200}
    assert fmp_call.calls == 1
    assert cache_write.calls == 1
    # 쓰여진 페이로드 검증
    written = cache_write.last_payload
    assert written["data"] == {"marketCap": 200}
    assert "cached_at" in written


def test_calls_fmp_on_stale_cache_and_writes_new():
    cached = {"cached_at": (NOW - timedelta(days=200)).isoformat(), "data": {"marketCap": 50}}
    cache_read = _Recorder(value=cached)
    cache_write = _Recorder()
    fmp_call = _Recorder(value={"marketCap": 300})

    result = load_or_fetch_pure(
        cache_read=cache_read,
        cache_write=cache_write,
        fmp_call=fmp_call,
        max_age_days=90,
        now=NOW,
    )

    assert result == {"marketCap": 300}
    assert fmp_call.calls == 1
    assert cache_write.calls == 1


def test_falls_back_to_stale_cache_on_fmp_failure():
    """FMPError → graceful degradation, stale 캐시라도 반환."""
    cached = {"cached_at": (NOW - timedelta(days=200)).isoformat(), "data": {"marketCap": 50}}
    cache_read = _Recorder(value=cached)
    cache_write = _Recorder()
    fmp_call = _Recorder(raises=FMPError("503"))

    result = load_or_fetch_pure(
        cache_read=cache_read,
        cache_write=cache_write,
        fmp_call=fmp_call,
        max_age_days=90,
        now=NOW,
    )

    assert result == {"marketCap": 50}
    assert cache_write.calls == 0


def test_returns_none_when_fmp_fails_and_no_cache():
    cache_read = _Recorder(value=None)
    cache_write = _Recorder()
    fmp_call = _Recorder(raises=FMPError("auth"))

    result = load_or_fetch_pure(
        cache_read=cache_read,
        cache_write=cache_write,
        fmp_call=fmp_call,
        now=NOW,
    )

    assert result is None
    assert cache_write.calls == 0


def test_returns_stale_cache_when_fmp_returns_empty():
    """FMP 응답이 빈 list 같은 경우(데이터 없음)도 stale 캐시 fallback."""
    cached = {"cached_at": (NOW - timedelta(days=200)).isoformat(), "data": {"marketCap": 50}}
    cache_read = _Recorder(value=cached)
    cache_write = _Recorder()
    fmp_call = _Recorder(value=None)

    result = load_or_fetch_pure(
        cache_read=cache_read,
        cache_write=cache_write,
        fmp_call=fmp_call,
        now=NOW,
    )

    assert result == {"marketCap": 50}
    assert cache_write.calls == 0


def test_returns_none_when_fmp_returns_empty_and_no_cache():
    cache_read = _Recorder(value=None)
    cache_write = _Recorder()
    fmp_call = _Recorder(value=None)

    result = load_or_fetch_pure(
        cache_read=cache_read,
        cache_write=cache_write,
        fmp_call=fmp_call,
        now=NOW,
    )

    assert result is None
    assert cache_write.calls == 0


def test_propagates_unexpected_exceptions():
    """FMPError 외 예외는 호출 측에 전파."""
    cache_read = _Recorder(value=None)
    cache_write = _Recorder()
    fmp_call = _Recorder(raises=RuntimeError("disk full"))

    with pytest.raises(RuntimeError, match="disk full"):
        load_or_fetch_pure(
            cache_read=cache_read,
            cache_write=cache_write,
            fmp_call=fmp_call,
            now=NOW,
        )
