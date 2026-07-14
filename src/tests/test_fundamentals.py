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


# ---------- 분기 statement wrapper 와이어링 ----------
#
# fetch_income_quarterly_with_cache / fetch_cashflow_quarterly_with_cache 는
# load_or_fetch_pure 의 wrapping — 핵심 로직은 위 테스트들이 covered. 본 섹션은
# wiring (어떤 fmp 메서드 호출하는지, 빈 list → None 승격, 결과 list 반환) 만
# 검증. moto/s3 통합 테스트는 #7-C 에서.


class _FakeFMPClient:
    """get_income_statement_quarterly / get_cash_flow_statement_quarterly 만
    대응하는 가짜 클라이언트. 호출 인자 검증용."""

    def __init__(
        self,
        income_payload: list[dict[str, Any]] | None = None,
        cashflow_payload: list[dict[str, Any]] | None = None,
    ):
        self.income_payload = income_payload or []
        self.cashflow_payload = cashflow_payload or []
        self.income_calls: list[tuple[str, int]] = []
        self.cashflow_calls: list[tuple[str, int]] = []

    def get_income_statement_quarterly(self, symbol: str, *, limit: int = 40):
        self.income_calls.append((symbol, limit))
        return self.income_payload

    def get_cash_flow_statement_quarterly(self, symbol: str, *, limit: int = 40):
        self.cashflow_calls.append((symbol, limit))
        return self.cashflow_payload


def test_fetch_income_quarterly_wires_through_load_or_fetch_pure(monkeypatch):
    """캐시 미스(read=None) + FMP 응답 → list 반환 + write 호출."""
    from common import fundamentals as f

    income = [{"date": "2026-03-31", "revenue": 95_000_000_000.0, "epsDiluted": 1.55}]
    fake = _FakeFMPClient(income_payload=income)

    written: dict[str, Any] = {}

    def fake_read(_bucket, _key):
        return None

    def fake_write(_bucket, key, payload, indent=None):
        written["key"] = key
        written["payload"] = payload

    monkeypatch.setattr(f, "read_json", fake_read)
    monkeypatch.setattr(f, "write_json", fake_write)

    result = f.fetch_income_quarterly_with_cache(
        fake,  # type: ignore[arg-type]
        "test-bucket",
        "AAPL",
        limit=24,
    )

    assert result == income
    assert fake.income_calls == [("AAPL", 24)]
    assert "income-statement-quarterly" in written["key"]
    assert "AAPL" in written["key"]
    assert written["payload"]["data"] == income


def test_normalize_income_rows_aliases_v3_field():
    """구 v3 표기(epsdiluted) → stable 표기(epsDiluted) alias.

    회귀 방지: v3 → stable 마이그레이션 때 소비 코드가 소문자 표기로 남아
    TTM EPS 가 전 종목 None 이 됐던 버그 (2026-07-14 발견)."""
    from common.fundamentals import normalize_income_rows

    rows = [
        {"date": "2026-03-31", "epsdiluted": 1.55},          # v3 시절 캐시
        {"date": "2025-12-31", "epsDiluted": 1.40},          # stable — 그대로
        {"date": "2025-09-30", "epsDiluted": 1.30, "epsdiluted": 9.99},  # 충돌 시 stable 우선
        {"date": "2025-06-30"},                              # 둘 다 없음 — 그대로
    ]
    result = normalize_income_rows(rows)

    assert result is rows  # in-place, 같은 리스트 반환
    assert result[0]["epsDiluted"] == 1.55
    assert result[1]["epsDiluted"] == 1.40
    assert result[2]["epsDiluted"] == 1.30
    assert "epsDiluted" not in result[3]


def test_fetch_income_quarterly_normalizes_v3_cached_rows(monkeypatch):
    """v3 시절 캐시(fresh, 소문자 표기)를 읽어도 epsDiluted 로 정규화되어 반환."""
    from common import fundamentals as f

    cached = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "data": [{"date": "2026-03-31", "revenue": 100.0, "epsdiluted": 1.55}],
    }
    fake = _FakeFMPClient()  # FMP 호출되면 빈 응답 — 캐시 hit 경로 검증

    monkeypatch.setattr(f, "read_json", lambda _b, _k: cached)
    monkeypatch.setattr(f, "write_json", lambda *a, **kw: None)

    result = f.fetch_income_quarterly_with_cache(
        fake,  # type: ignore[arg-type]
        "test-bucket",
        "AAPL",
    )

    assert result[0]["epsDiluted"] == 1.55
    assert fake.income_calls == []  # fresh 캐시 — FMP 미호출


def test_fetch_cashflow_quarterly_wires_through_load_or_fetch_pure(monkeypatch):
    from common import fundamentals as f

    cashflow = [{"date": "2026-03-31", "freeCashFlow": 25_000_000_000.0}]
    fake = _FakeFMPClient(cashflow_payload=cashflow)

    monkeypatch.setattr(f, "read_json", lambda *a, **k: None)
    written_keys: list[str] = []
    monkeypatch.setattr(
        f, "write_json", lambda _b, key, _p, indent=None: written_keys.append(key)
    )

    result = f.fetch_cashflow_quarterly_with_cache(
        fake,  # type: ignore[arg-type]
        "test-bucket",
        "AAPL",
        limit=24,
    )

    assert result == cashflow
    assert fake.cashflow_calls == [("AAPL", 24)]
    assert "cash-flow-statement-quarterly" in written_keys[0]


def test_fetch_quarterly_returns_empty_list_when_fmp_empty_and_no_cache(monkeypatch):
    """FMP 가 빈 list 반환 + 캐시 없음 → 빈 list (None 아님)."""
    from common import fundamentals as f

    fake = _FakeFMPClient(income_payload=[])

    monkeypatch.setattr(f, "read_json", lambda *a, **k: None)
    monkeypatch.setattr(f, "write_json", lambda *a, **k: None)

    result = f.fetch_income_quarterly_with_cache(fake, "b", "AAPL")  # type: ignore[arg-type]
    assert result == []
    # 빈 응답이라 cache write 도 안 일어남 (load_or_fetch_pure 정책)


def test_fetch_quarterly_returns_cached_list_on_fresh_hit(monkeypatch):
    from common import fundamentals as f

    cached = {
        "cached_at": (NOW - timedelta(days=10)).isoformat(),
        "data": [{"date": "2026-03-31", "revenue": 100.0}],
    }
    fake = _FakeFMPClient()  # FMP 호출되면 빈 list — 캐시 hit 이어야

    monkeypatch.setattr(f, "read_json", lambda *a, **k: cached)
    monkeypatch.setattr(f, "write_json", lambda *a, **k: None)

    result = f.fetch_income_quarterly_with_cache(fake, "b", "AAPL")  # type: ignore[arg-type]
    assert result == cached["data"]
    assert fake.income_calls == []  # FMP 호출 X (캐시 hit)
