"""peer_context 단위 테스트.

순수 함수만 — 네트워크/S3/AWS 호출 없음.
"""
from __future__ import annotations

from datetime import date

from common.models import Constituent
from screening.peer_context import attach_peer_context
from screening.schemas import FactorScores, ScreenedStock


# ---------- 헬퍼 ----------


def _c(symbol: str, sub_sector: str | None = "SW", sector: str | None = "Tech") -> Constituent:
    return Constituent(
        symbol=symbol,
        sector=sector,
        sub_sector=sub_sector,
        date_added=date(2010, 1, 1),
    )


def _fs(z: float = 0.0, **kw) -> FactorScores:
    return FactorScores(momentum_z=z, value_z=z, **kw)


def _pool(symbol: str, *, sub_sector: str = "SW", z: float = 0.0, **kw) -> tuple[Constituent, FactorScores]:
    return (_c(symbol, sub_sector=sub_sector), _fs(z=z, **kw))


def _selected(
    symbol: str,
    sub_sector: str | None = "SW",
    rank: int = 1,
    composite: float = 1.0,
) -> ScreenedStock:
    return ScreenedStock(
        symbol=symbol,
        sector="Tech",
        sub_sector=sub_sector,
        rank=rank,
        composite_score=composite,
        factors=FactorScores(),
    )


# ---------- 기본 동작 ----------


def test_attaches_peers_from_same_sub_sector():
    pool = [_pool(f"P{i:02d}", z=1.0 - i * 0.1) for i in range(5)]
    result = attach_peer_context([_selected("X")], pool)

    assert len(result[0].peer_context) == 5
    # composite desc → P00 최고, P04 최저
    assert [p.symbol for p in result[0].peer_context] == ["P00", "P01", "P02", "P03", "P04"]


def test_excludes_self_from_peers_when_selected_in_pool():
    pool = [
        (_c("X", sub_sector="SW"), _fs(z=2.0)),  # selected 자신
        _pool("P1", z=1.0),
        _pool("P2", z=0.5),
    ]
    result = attach_peer_context([_selected("X")], pool)
    symbols = [p.symbol for p in result[0].peer_context]
    assert "X" not in symbols
    assert symbols == ["P1", "P2"]


def test_caps_at_n_peers():
    pool = [_pool(f"P{i:02d}", z=1.0 - i * 0.05) for i in range(10)]
    result = attach_peer_context([_selected("X")], pool, n_peers=5)
    assert len(result[0].peer_context) == 5


def test_under_n_peers_when_sub_sector_small():
    pool = [_pool(f"P{i}", z=1.0 - i * 0.1) for i in range(3)]
    result = attach_peer_context([_selected("X")], pool)
    assert len(result[0].peer_context) == 3


def test_empty_when_no_peers_in_sub_sector():
    pool = [_pool(f"P{i}", sub_sector="HW", z=1.0) for i in range(5)]
    result = attach_peer_context([_selected("X", sub_sector="SW")], pool)
    assert result[0].peer_context == []


def test_empty_when_selected_sub_sector_is_none():
    pool = [_pool(f"P{i}", z=1.0) for i in range(5)]
    result = attach_peer_context([_selected("X", sub_sector=None)], pool)
    assert result[0].peer_context == []


def test_pool_entries_with_none_sub_sector_are_skipped():
    pool = [
        (_c("VALID", sub_sector="SW"), _fs(z=1.0)),
        (_c("INVALID", sub_sector=None), _fs(z=2.0)),  # 더 높은 점수지만 sub_sector 없음
    ]
    result = attach_peer_context([_selected("X", sub_sector="SW")], pool)
    symbols = [p.symbol for p in result[0].peer_context]
    assert "VALID" in symbols
    assert "INVALID" not in symbols


# ---------- 정렬 ----------


def test_peers_sorted_by_composite_desc():
    pool = [
        _pool("LOW", z=0.1),
        _pool("HIGH", z=2.0),
        _pool("MID", z=1.0),
    ]
    result = attach_peer_context([_selected("X")], pool)
    assert [p.symbol for p in result[0].peer_context] == ["HIGH", "MID", "LOW"]


def test_tiebreaker_uses_alphabetical_symbol():
    pool = [
        _pool("ZZZ", z=1.0),
        _pool("AAA", z=1.0),
        _pool("MMM", z=1.0),
    ]
    result = attach_peer_context([_selected("X")], pool)
    assert [p.symbol for p in result[0].peer_context] == ["AAA", "MMM", "ZZZ"]


# ---------- PeerComparable 필드 ----------


def test_peer_carries_value_multiples():
    pool = [
        (
            _c("P1"),
            _fs(z=1.0, pe_ttm=20.0, ev_ebitda=10.0, fcf_yield=0.05),
        ),
    ]
    result = attach_peer_context([_selected("X")], pool)
    peer = result[0].peer_context[0]
    assert peer.symbol == "P1"
    assert peer.pe_ttm == 20.0
    assert peer.ev_ebitda == 10.0
    assert peer.fcf_yield == 0.05


def test_peer_carries_none_for_missing_multiples():
    pool = [(_c("P1"), _fs(z=1.0))]  # 멀티플 미지정 → None
    result = attach_peer_context([_selected("X")], pool)
    peer = result[0].peer_context[0]
    assert peer.pe_ttm is None
    assert peer.ev_ebitda is None
    assert peer.fcf_yield is None


# ---------- 다중 selected ----------


def test_handles_multiple_selected_with_different_sub_sectors():
    pool = (
        [_pool(f"S{i}", sub_sector="SW", z=1.0 - i * 0.1) for i in range(5)]
        + [_pool(f"H{i}", sub_sector="HW", z=1.0 - i * 0.1) for i in range(5)]
    )
    selected = [
        _selected("X1", sub_sector="SW", rank=1),
        _selected("X2", sub_sector="HW", rank=2),
    ]
    result = attach_peer_context(selected, pool)

    # 각 selected 가 자기 sub_sector 의 피어만 받음
    assert all(p.symbol.startswith("S") for p in result[0].peer_context)
    assert all(p.symbol.startswith("H") for p in result[1].peer_context)


# ---------- 불변성 / 다른 필드 보존 ----------


def test_does_not_mutate_input_selected():
    pool = [_pool("P1", z=1.0)]
    s = _selected("X")
    snapshot = s.model_copy()
    attach_peer_context([s], pool)
    assert s == snapshot


def test_preserves_other_fields_on_returned_stock():
    pool = [_pool("P1", z=1.0, pe_ttm=15.0)]
    s = ScreenedStock(
        symbol="X",
        company_name="X Inc.",
        sector="Tech",
        sub_sector="SW",
        rank=3,
        composite_score=1.5,
        factors=FactorScores(momentum_z=1.5, value_z=1.5, pe_ttm=18.0),
        data_quality_flags=["missing_value"],
    )
    result = attach_peer_context([s], pool)[0]
    assert result.symbol == "X"
    assert result.company_name == "X Inc."
    assert result.rank == 3
    assert result.composite_score == 1.5
    assert result.factors.pe_ttm == 18.0
    assert result.data_quality_flags == ["missing_value"]
    # peer_context 만 새로 채워짐
    assert len(result.peer_context) == 1


def test_n_peers_above_max_length_raises():
    """n_peers > 5 (ScreenedStock.peer_context max_length) 면 함수 진입 시점에 차단.

    model_copy(update=) 가 재검증을 하지 않아 schema 검증을 통과해버리므로,
    함수가 자체적으로 가드.
    """
    import pytest

    pool = [_pool(f"P{i}", z=1.0 - i * 0.05) for i in range(10)]
    with pytest.raises(ValueError, match="max_length"):
        attach_peer_context([_selected("X")], pool, n_peers=6)
