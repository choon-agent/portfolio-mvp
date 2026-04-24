"""구성종목 비즈니스 로직 단위 테스트.

커버리지: FMP 파싱, diff 계산, Arrow 왕복 변환.
AWS 없음, 네트워크 없음 — 순수 함수만 테스트.
"""
from __future__ import annotations

from datetime import date

from common.models import Constituent
from screening.constituents import (
    arrow_to_constituents,
    build_constituents,
    compute_diff,
    constituents_to_arrow,
    diff_to_events,
)


def test_build_constituents_marks_current_members_with_none_date_removed():
    current = [
        {"symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology",
         "subSector": "Consumer Electronics", "cik": "0000320193",
         "dateFirstAdded": "1982-11-30"},
    ]
    historical: list[dict] = []

    result = build_constituents(current, historical)

    assert len(result) == 1
    assert result[0].symbol == "AAPL"
    assert result[0].date_removed is None
    assert result[0].is_current is True


def test_build_constituents_creates_removal_records_from_history():
    current: list[dict] = []
    historical = [
        {
            "dateAdded": "2008-09-22",
            "symbol": "KMI",
            "removedTicker": "LEH",
            "removedSecurity": "Lehman Brothers",
        }
    ]

    result = build_constituents(current, historical)

    removed = [c for c in result if c.symbol == "LEH"]
    assert len(removed) == 1
    assert removed[0].date_removed == date(2008, 9, 22)
    assert removed[0].is_current is False


def test_compute_diff_detects_added_and_removed():
    old = [
        Constituent(symbol="AAPL", date_added=date(1982, 11, 30)),
        Constituent(symbol="WHR", date_added=date(1990, 1, 1)),
    ]
    new = [
        Constituent(symbol="AAPL", date_added=date(1982, 11, 30)),
        Constituent(symbol="SMCI", date_added=date(2024, 3, 18)),
    ]

    diff = compute_diff(old, new)

    assert diff.added_symbols == ["SMCI"]
    assert diff.removed_symbols == ["WHR"]
    assert diff.has_changes is True


def test_compute_diff_no_changes_when_states_identical():
    state = [Constituent(symbol="AAPL", date_added=date(1982, 11, 30))]
    diff = compute_diff(state, state)
    assert diff.has_changes is False


def test_compute_diff_detects_metadata_change():
    old = [Constituent(symbol="AAPL", company_name="Apple Inc", sector="Tech",
                       date_added=date(1982, 11, 30))]
    new = [Constituent(symbol="AAPL", company_name="Apple Inc.", sector="Technology",
                       date_added=date(1982, 11, 30))]
    diff = compute_diff(old, new)
    assert [c.symbol for c in diff.metadata_changed] == ["AAPL"]


def test_compute_diff_against_none_treats_everything_as_added():
    new = [Constituent(symbol="AAPL", date_added=date(1982, 11, 30))]
    diff = compute_diff(None, new)
    assert diff.added_symbols == ["AAPL"]
    assert diff.removed_symbols == []


def test_compute_diff_ignores_historical_removals_in_new_state():
    """새 상태에 들어있는 과거 편출 기록은 'removed' 로 잡히면 안 됨."""
    old = [Constituent(symbol="AAPL", date_added=date(1982, 11, 30))]
    new = [
        Constituent(symbol="AAPL", date_added=date(1982, 11, 30)),
        Constituent(symbol="LEH", date_added=date(1984, 4, 2),
                    date_removed=date(2008, 9, 22)),  # 과거 기록
    ]
    diff = compute_diff(old, new)
    assert diff.has_changes is False


def test_arrow_roundtrip_preserves_data():
    original = [
        Constituent(symbol="AAPL", company_name="Apple Inc.", sector="Technology",
                    sub_sector="Consumer Electronics", cik="0000320193",
                    date_added=date(1982, 11, 30), date_removed=None),
        Constituent(symbol="LEH", company_name="Lehman Brothers",
                    date_added=date(1984, 4, 2), date_removed=date(2008, 9, 22)),
    ]
    table = constituents_to_arrow(original)
    recovered = arrow_to_constituents(table)
    assert recovered == original


def test_diff_to_events_preserves_run_date():
    diff = compute_diff(
        [Constituent(symbol="WHR", date_added=date(1990, 1, 1))],
        [Constituent(symbol="SMCI", date_added=date(2024, 3, 18))],
    )
    events = diff_to_events(diff, event_date=date(2026, 4, 21))
    assert {e.event_type for e in events} == {"added", "removed"}
    assert all(e.event_date == date(2026, 4, 21) for e in events)