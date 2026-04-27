"""FMP key-metrics-ttm cache-aside 조회.

설계: docs/01-screening.md §2.1 (단일 엔드포인트 — P/E·EV/EBITDA·FCF Yield + marketCap)

캐시 정책 (CLAUDE.md "분기 재무는 90일 캐시"):
- S3 hit + fresh → 캐시 데이터 반환 (FMP 호출 없음)
- S3 miss/stale → FMP 호출 → S3 저장 → 반환
- FMP 실패 시 stale 캐시라도 있으면 반환 (graceful degradation)

S3 객체 형식 (자기 기술적):
  {
    "cached_at": "2026-04-27T10:00:00Z",
    "data": {...FMP 응답 첫 항목...}
  }

LLM 사용: 없음.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from common.fmp_client import FMPClient, FMPError
from common.s3_io import read_json, write_json

logger = logging.getLogger(__name__)

DEFAULT_CACHE_PREFIX = "metadata/fundamentals/key-metrics-ttm"
DEFAULT_CACHE_MAX_AGE_DAYS = 90


# ---------- 순수 로직 (테스트 친화) ----------


def is_cache_fresh(cache_entry: Any, max_age_days: int, *, now: datetime | None = None) -> bool:
    """캐시 항목이 max_age_days 이내인지.

    cache_entry 는 {"cached_at": ISO_str, "data": ...} 형식.
    파싱 실패/타임스탬프 없음 → False (stale 취급).
    """
    if not isinstance(cache_entry, dict):
        return False
    ts_str = cache_entry.get("cached_at")
    if not isinstance(ts_str, str):
        return False
    try:
        ts = datetime.fromisoformat(ts_str)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (current - ts) <= timedelta(days=max_age_days)


def _cache_key(prefix: str, symbol: str) -> str:
    """심볼별 S3 캐시 키 (FMP 와 동일하게 dual-class 는 하이픈)."""
    fmp_symbol = symbol.replace(".", "-")
    return f"{prefix}/symbol={fmp_symbol}.json"


def load_or_fetch_pure(
    *,
    cache_read: Callable[[], Any | None],
    cache_write: Callable[[dict[str, Any]], None],
    fmp_call: Callable[[], dict[str, Any] | None],
    max_age_days: int = DEFAULT_CACHE_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """순수 cache-aside 로직 (S3·FMP 의존 없이 callable 만 받음).

    동작:
      1. cache_read → fresh 면 데이터 반환
      2. fmp_call. 성공 + 데이터 → cache_write 후 반환
      3. fmp_call 실패 또는 빈 응답 → stale 캐시라도 있으면 반환, 아니면 None

    fmp_call 은 FMPError 외 예외도 던질 수 있음 — 호출 측이 전파/억제 결정.
    """
    cached = cache_read()
    if is_cache_fresh(cached, max_age_days, now=now):
        return cached["data"] if isinstance(cached, dict) else None

    try:
        data = fmp_call()
    except FMPError as exc:
        logger.warning("FMP 호출 실패 — stale 캐시로 fallback (%s)", exc)
        return cached["data"] if isinstance(cached, dict) and "data" in cached else None

    if data is None:
        return cached["data"] if isinstance(cached, dict) and "data" in cached else None

    payload = {
        "cached_at": (now or datetime.now(timezone.utc)).isoformat(),
        "data": data,
    }
    cache_write(payload)
    return data


# ---------- S3 + FMP 바인딩 (Lambda 사용) ----------


def fetch_with_cache(
    fmp: FMPClient,
    bucket: str,
    symbol: str,
    *,
    prefix: str = DEFAULT_CACHE_PREFIX,
    max_age_days: int = DEFAULT_CACHE_MAX_AGE_DAYS,
) -> dict[str, Any] | None:
    """심볼 1개의 key-metrics-ttm 응답을 cache-aside 로 가져옴.

    반환: FMP 응답의 첫 항목 (FMP 가 list[dict] 로 반환). 없으면 None.

    주의:
      - 호출당 최대 1회 S3 read + 최대 1회 FMP + 최대 1회 S3 write
      - 일반적으로 분기 발표 후 첫 호출 시에만 FMP — 이후 90일은 S3 hit
    """
    key = _cache_key(prefix, symbol)

    def _fmp_call() -> dict[str, Any] | None:
        resp = fmp.get_key_metrics_ttm(symbol)
        return resp[0] if resp else None

    return load_or_fetch_pure(
        cache_read=lambda: read_json(bucket, key),
        cache_write=lambda payload: write_json(bucket, key, payload, indent=None),
        fmp_call=_fmp_call,
        max_age_days=max_age_days,
    )
