"""스크리닝 스키마 단위 테스트.

순수 Pydantic 검증만 — 네트워크, S3, AWS 호출 없음.
"""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from screening.schemas import (
    FactorScores,
    PeerComparable,
    ScreenedStock,
    ScreeningResult,
)


# ---------- 헬퍼 ----------


def _stock(rank: int, score: float, symbol: str | None = None) -> ScreenedStock:
    return ScreenedStock(
        symbol=symbol or f"SYM{rank:02d}",
        rank=rank,
        composite_score=score,
        factors=FactorScores(),
    )


def _result(n: int) -> ScreeningResult:
    return ScreeningResult(
        as_of_date=date(2026, 5, 4),
        universe_size=480,
        selected=[_stock(i, 1.0 - i * 0.01) for i in range(1, n + 1)],
        factor_weights={"momentum": 0.5, "value": 0.5},
        run_id="2026-05-04T00:00:00Z",
    )


# ---------- FactorScores ----------


def test_factor_scores_all_fields_optional():
    scores = FactorScores()
    assert scores.momentum_12_1m is None
    assert scores.value_z is None


def test_factor_scores_accepts_partial_population():
    scores = FactorScores(momentum_12_1m=0.15, momentum_z=1.2)
    assert scores.momentum_12_1m == 0.15
    assert scores.momentum_z == 1.2
    assert scores.value_z is None


# ---------- PeerComparable ----------


def test_peer_comparable_symbol_required():
    with pytest.raises(ValidationError):
        PeerComparable()  # type: ignore[call-arg]


def test_peer_comparable_multiples_optional():
    peer = PeerComparable(symbol="AAPL")
    assert peer.pe_ttm is None
    assert peer.ev_ebitda is None
    assert peer.fcf_yield is None


# ---------- ScreenedStock ----------


def test_screened_stock_rank_must_be_positive():
    with pytest.raises(ValidationError):
        _stock(rank=0, score=1.0)


def test_screened_stock_defaults_for_optional_collections():
    s = _stock(rank=1, score=1.0)
    assert s.peer_context == []
    assert s.data_quality_flags == []


def test_screened_stock_peer_context_capped_at_5():
    peers = [PeerComparable(symbol=f"P{i}") for i in range(6)]
    with pytest.raises(ValidationError):
        ScreenedStock(
            symbol="AAPL",
            rank=1,
            composite_score=1.0,
            factors=FactorScores(),
            peer_context=peers,
        )


# ---------- ScreeningResult ----------


def test_screening_result_accepts_production_sizes():
    """생산 정책 15~20 은 schema 가 직접 강제하지 않지만 모두 valid."""
    assert len(_result(15).selected) == 15
    assert len(_result(20).selected) == 20


def test_screening_result_accepts_dry_run_sizes():
    """dry-run/테스트용 작은 크기도 schema 통과 (1 이상)."""
    assert len(_result(3).selected) == 3
    assert len(_result(5).selected) == 5


def test_screening_result_rejects_empty_selected():
    with pytest.raises(ValidationError):
        _result(0)


def test_screening_result_rejects_above_schema_max():
    """schema 상한 50 — 향후 확장 여지지만 비현실적 크기는 차단."""
    with pytest.raises(ValidationError):
        _result(51)


def test_screening_result_enforces_consecutive_rank_order():
    # 정상 결과의 6번째 종목 rank만 어긋나게 변형
    selected = [_stock(rank=i, score=1.0 - i * 0.01) for i in range(1, 16)]
    selected[5] = ScreenedStock(
        symbol="WRONG",
        rank=99,
        composite_score=selected[5].composite_score,
        factors=FactorScores(),
    )
    with pytest.raises(ValidationError, match="rank 불일치"):
        ScreeningResult(
            as_of_date=date(2026, 5, 4),
            universe_size=480,
            selected=selected,
            factor_weights={"momentum": 0.5, "value": 0.5},
            run_id="run-1",
        )


def test_screening_result_enforces_descending_composite_score():
    # rank 는 1..15 순서, score 는 오름차순 → 실패
    selected = [
        ScreenedStock(
            symbol=f"S{i}",
            rank=i,
            composite_score=i * 0.1,
            factors=FactorScores(),
        )
        for i in range(1, 16)
    ]
    with pytest.raises(ValidationError, match="내림차순"):
        ScreeningResult(
            as_of_date=date(2026, 5, 4),
            universe_size=480,
            selected=selected,
            factor_weights={"momentum": 0.5, "value": 0.5},
            run_id="run-1",
        )


def test_screening_result_json_roundtrip_preserves_data():
    original = _result(15)
    serialized = original.model_dump_json()
    restored = ScreeningResult.model_validate_json(serialized)
    assert restored == original
