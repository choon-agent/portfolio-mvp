"""구성종목 비즈니스 로직.

순수 함수 — 네트워크, S3, AWS 호출 없음. 단위 테스트 완전 가능.

책임:
1. FMP 의 current + historical 응답을 표준 Constituent 리스트로 병합
2. 두 상태 간 diff 계산
3. S3 영속화를 위한 Arrow 테이블 변환

LLM 사용: 없음.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import pyarrow as pa

from common.models import Constituent, ConstituentChangeEvent, DiffResult

logger = logging.getLogger(__name__)


# ---------- 파싱 ----------


def _parse_date(value: Any) -> date | None:
    """FMP 날짜는 'YYYY-MM-DD' 문자열로 옴. None 과 빈 문자열 모두 허용."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            logger.warning("날짜 파싱 실패: %r", value)
            return None
    return None


def build_constituents(
    current_response: list[dict[str, Any]],
    historical_response: list[dict[str, Any]],
) -> list[Constituent]:
    """FMP current + historical 엔드포인트를 표준 Constituent 리스트로 병합.

    로직:
    - `current` 부터 시작: 여기의 모든 종목은 현재 구성원 (date_removed=None)
    - `historical` 이벤트 순회: 각 이벤트는 편출을 의미
      ('removedTicker' 는 'dateAdded' 시점에 교체 종목이 추가되면서 지수에서 빠짐)
    - 각 과거 편출에 대해 date_removed 가 설정된 Constituent 레코드 생성
    - 한 종목이 이력에 여러 번 등장할 수 있음 (재편입) — 가장 최근(현재) 항목과
      구별되는 과거 재임 기록을 모두 유지

    FMP historical 스키마 참고 (2026 기준 실측):
      {
        "dateAdded": "2024-03-18",       # 변경이 발생한 날짜
        "addedSecurity": "Super Micro...",
        "removedTicker": "WHR",          # 지수에서 빠진 종목
        "removedSecurity": "Whirlpool Corporation",
        "symbol": "SMCI",                # 편입된 종목
        ...
      }
    """
    constituents: list[Constituent] = []
    seen_current: set[str] = set()

    # 현재 구성원
    for row in current_response:
        symbol = row.get("symbol")
        if not symbol:
            continue
        seen_current.add(symbol)
        constituents.append(
            Constituent(
                symbol=symbol,
                company_name=row.get("name"),
                sector=row.get("sector"),
                sub_sector=row.get("subSector"),
                cik=str(row["cik"]) if row.get("cik") else None,
                date_added=_parse_date(row.get("dateFirstAdded"))
                or date(1970, 1, 1),  # 오래된 항목에서 날짜 누락 시 기본값
                date_removed=None,
            )
        )

    # 과거 편출 — 각 이벤트: removed_ticker 가 event_date 에 지수에서 빠짐
    for event in historical_response:
        removed_ticker = event.get("removedTicker")
        event_date = _parse_date(event.get("dateAdded"))
        if not removed_ticker or not event_date:
            continue
        # historical 엔드포인트만으로는 원래의 date_added 를 알 수 없음;
        # FMP historical 은 편출 날짜만 제공. date_added 는 센티넬 값으로 둠.
        constituents.append(
            Constituent(
                symbol=removed_ticker,
                company_name=event.get("removedSecurity"),
                sector=None,
                sub_sector=None,
                cik=None,
                date_added=date(1970, 1, 1),  # 이 엔드포인트에서는 알 수 없음
                date_removed=event_date,
            )
        )

    return constituents


# ---------- Diff ----------


def compute_diff(
    old: list[Constituent] | None,
    new: list[Constituent],
) -> DiffResult:
    """두 구성종목 리스트 비교. CURRENT 구성원(date_removed=None)에 집중.

    - 'added': 지금 구성원이지만 이전에는 아니었음
    - 'removed': 이전에는 구성원이었지만 지금은 아님
    - 'metadata_changed': 양쪽 다 현재 구성원이지만 섹터/이름 변경됨
    """
    old_current = {c.symbol: c for c in (old or []) if c.is_current}
    new_current = {c.symbol: c for c in new if c.is_current}

    added_symbols = set(new_current) - set(old_current)
    removed_symbols = set(old_current) - set(new_current)

    added = [new_current[s] for s in sorted(added_symbols)]
    removed = [old_current[s] for s in sorted(removed_symbols)]

    metadata_changed: list[Constituent] = []
    for symbol in sorted(set(old_current) & set(new_current)):
        o, n = old_current[symbol], new_current[symbol]
        if (o.company_name, o.sector, o.sub_sector) != (n.company_name, n.sector, n.sub_sector):
            metadata_changed.append(n)

    return DiffResult(added=added, removed=removed, metadata_changed=metadata_changed)


def diff_to_events(diff: DiffResult, event_date: date) -> list[ConstituentChangeEvent]:
    """DiffResult 를 append-only 이벤트 로그 항목으로 평탄화."""
    events: list[ConstituentChangeEvent] = []
    for c in diff.added:
        events.append(
            ConstituentChangeEvent(
                event_date=event_date,
                event_type="added",
                symbol=c.symbol,
                company_name=c.company_name,
                sector=c.sector,
                new_date_added=c.date_added,
            )
        )
    for c in diff.removed:
        events.append(
            ConstituentChangeEvent(
                event_date=event_date,
                event_type="removed",
                symbol=c.symbol,
                company_name=c.company_name,
                sector=c.sector,
                previous_date_added=c.date_added,
            )
        )
    for c in diff.metadata_changed:
        events.append(
            ConstituentChangeEvent(
                event_date=event_date,
                event_type="metadata_updated",
                symbol=c.symbol,
                company_name=c.company_name,
                sector=c.sector,
            )
        )
    return events


# ---------- Arrow 변환 ----------


CONSTITUENTS_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("company_name", pa.string()),
        ("sector", pa.string()),
        ("sub_sector", pa.string()),
        ("cik", pa.string()),
        ("date_added", pa.date32()),
        ("date_removed", pa.date32()),
    ]
)

CHANGE_EVENTS_SCHEMA = pa.schema(
    [
        ("event_date", pa.date32()),
        ("event_type", pa.string()),
        ("symbol", pa.string()),
        ("company_name", pa.string()),
        ("sector", pa.string()),
        ("previous_date_added", pa.date32()),
        ("new_date_added", pa.date32()),
    ]
)


def constituents_to_arrow(constituents: list[Constituent]) -> pa.Table:
    data = {name: [] for name in CONSTITUENTS_SCHEMA.names}
    for c in constituents:
        data["symbol"].append(c.symbol)
        data["company_name"].append(c.company_name)
        data["sector"].append(c.sector)
        data["sub_sector"].append(c.sub_sector)
        data["cik"].append(c.cik)
        data["date_added"].append(c.date_added)
        data["date_removed"].append(c.date_removed)
    return pa.table(data, schema=CONSTITUENTS_SCHEMA)


def arrow_to_constituents(table: pa.Table) -> list[Constituent]:
    rows = table.to_pylist()
    return [Constituent(**row) for row in rows]


def events_to_arrow(events: list[ConstituentChangeEvent]) -> pa.Table:
    data = {name: [] for name in CHANGE_EVENTS_SCHEMA.names}
    for e in events:
        data["event_date"].append(e.event_date)
        data["event_type"].append(e.event_type)
        data["symbol"].append(e.symbol)
        data["company_name"].append(e.company_name)
        data["sector"].append(e.sector)
        data["previous_date_added"].append(e.previous_date_added)
        data["new_date_added"].append(e.new_date_added)
    return pa.table(data, schema=CHANGE_EVENTS_SCHEMA)