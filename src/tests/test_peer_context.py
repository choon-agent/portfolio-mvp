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


def test_falls_back_to_sector_when_no_subsector_peers():
    """sub_sector "SW" 에 peer 가 없어도 같은 sector "Tech" 에서 5개 끌어옴."""
    pool = [_pool(f"P{i}", sub_sector="HW", z=1.0 - i * 0.1) for i in range(5)]
    result = attach_peer_context([_selected("X", sub_sector="SW")], pool)
    # selected 의 sector="Tech" + pool 모두 sector="Tech" → sector 폴백으로 5개 충족
    assert len(result[0].peer_context) == 5
    assert {p.symbol for p in result[0].peer_context} == {f"P{i}" for i in range(5)}


def test_falls_back_to_sector_when_selected_subsector_is_none():
    """selected 가 sub_sector 모르더라도 sector 가 있으면 sector 폴백."""
    pool = [_pool(f"P{i}", z=1.0 - i * 0.1) for i in range(5)]  # sector="Tech", sub_sector="SW"
    result = attach_peer_context([_selected("X", sub_sector=None)], pool)
    # selected.sector="Tech" → sector 폴백 가능
    assert len(result[0].peer_context) == 5


def test_empty_when_both_subsector_and_sector_unmatched():
    """selected 의 sector 도 pool 에 없으면 빈 peer_context."""
    pool = [
        (_c(f"P{i}", sector="DifferentSector", sub_sector="DifferentSub"), _fs(z=1.0))
        for i in range(5)
    ]
    result = attach_peer_context([_selected("X", sub_sector="SW")], pool)
    assert result[0].peer_context == []


def test_pool_entries_with_none_sub_sector_excluded_from_subsector_grouping():
    """sub_sector=None 인 pool 항목은 sub_sector peer 후보에서 제외."""
    pool = [
        (_c("VALID", sub_sector="SW"), _fs(z=1.0)),
        (_c("INVALID", sub_sector=None), _fs(z=2.0)),  # 더 높은 점수지만 sub_sector 없음
    ]
    result = attach_peer_context([_selected("X", sub_sector="SW")], pool)
    symbols = {p.symbol for p in result[0].peer_context}
    # VALID 는 sub_sector peer 로 포함, INVALID 는 (sector="Tech" 가 같으므로) sector 폴백으로 들어옴
    # sub_sector peer 가 1 < n_peers=5 라 sector 폴백 발동
    assert "VALID" in symbols
    assert "INVALID" in symbols  # sector 폴백으로 들어옴 — sector "Tech" 매칭


def test_pool_entries_with_none_sector_excluded_from_sector_fallback():
    """sub_sector=None AND sector=None 인 pool 항목은 어떤 폴백에도 안 들어옴."""
    pool = [
        (_c("VALID", sub_sector="SW"), _fs(z=1.0)),
        (_c("ORPHAN", sector=None, sub_sector=None), _fs(z=5.0)),  # 그룹 키 모두 없음
    ]
    result = attach_peer_context([_selected("X", sub_sector="SW")], pool)
    symbols = {p.symbol for p in result[0].peer_context}
    assert "VALID" in symbols
    assert "ORPHAN" not in symbols


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


# ---------- sector 폴백 (dry-run 30종목 발견 사례) ----------


def test_falls_back_to_sector_when_subsector_is_singleton():
    """선정 종목이 sub_sector 의 유일한 멤버여도 sector 폴백으로 peer 확보.

    실 운영 사례 (2026-04-28 dry-run): MPC ("Oil & Gas Refining & Marketing" 1종목),
    USB ("Banks - Regional" 1종목) 가 빈 peer_context → sector 에서 보충.
    """
    # 같은 sub_sector "RareSub" 에 selected 자기 1종목뿐 (pool 에 안 들어감)
    # sector "EnergySim" 에는 다른 4개 종목
    pool = [
        (_c(f"P{i}", sector="EnergySim", sub_sector="OtherSub"), _fs(z=1.0 - i * 0.1))
        for i in range(4)
    ]
    selected = ScreenedStock(
        symbol="LONELY",
        sector="EnergySim",
        sub_sector="RareSub",
        rank=1,
        composite_score=1.0,
        factors=FactorScores(),
    )
    result = attach_peer_context([selected], pool)

    assert len(result[0].peer_context) == 4
    assert {p.symbol for p in result[0].peer_context} == {f"P{i}" for i in range(4)}


def test_subsector_peers_listed_before_sector_extras():
    """sub_sector peer 와 sector 폴백 peer 가 섞일 때 sub_sector 가 먼저."""
    pool = [
        # sub_sector "SW" 에 2개 (selected 와 같은 sub_sector)
        (_c("S1", sector="Tech", sub_sector="SW"), _fs(z=0.1)),
        (_c("S2", sector="Tech", sub_sector="SW"), _fs(z=0.0)),
        # sector "Tech" 의 다른 sub_sector 에 5개 (점수 더 높음 — 그래도 sub_sector 우선)
        (_c("H1", sector="Tech", sub_sector="HW"), _fs(z=2.0)),
        (_c("H2", sector="Tech", sub_sector="HW"), _fs(z=1.9)),
        (_c("H3", sector="Tech", sub_sector="HW"), _fs(z=1.8)),
    ]
    selected = _selected("X", sub_sector="SW")  # sector="Tech"
    result = attach_peer_context([selected], pool)

    peers = result[0].peer_context
    # n_peers=5: sub_sector "SW" peer 2개 → 부족 3개를 sector "Tech" 에서 보충
    # sub_sector peer 가 먼저, 이어서 sector extras
    assert len(peers) == 5
    # 첫 2개는 sub_sector "SW" 멤버 (점수 낮아도 우선)
    assert {peers[0].symbol, peers[1].symbol} == {"S1", "S2"}
    # 나머지 3개는 sector 폴백 — H1/H2/H3 (점수 desc)
    assert [peers[i].symbol for i in range(2, 5)] == ["H1", "H2", "H3"]


def test_subsector_only_when_sufficient_no_sector_fallback():
    """sub_sector 만으로 n_peers 충족하면 sector 폴백 안 함 (점수 낮은 sector peer 무시)."""
    pool = [
        # sub_sector 5개로 충분
        *[(_c(f"S{i}", sector="Tech", sub_sector="SW"), _fs(z=1.0 - i * 0.1)) for i in range(5)],
        # sector 의 다른 sub_sector — 점수 매우 높지만 들어오면 안 됨
        (_c("HOT", sector="Tech", sub_sector="HW"), _fs(z=10.0)),
    ]
    selected = _selected("X", sub_sector="SW")
    result = attach_peer_context([selected], pool)

    peer_symbols = {p.symbol for p in result[0].peer_context}
    assert peer_symbols == {f"S{i}" for i in range(5)}
    assert "HOT" not in peer_symbols


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
