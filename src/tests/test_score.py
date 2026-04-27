"""점수·랭킹·선택 단위 테스트.

순수 함수만 — 네트워크/S3/AWS 호출 없음.
"""
from __future__ import annotations

from datetime import date

import pytest

from common.models import Constituent
from screening.schemas import FactorScores
from screening.score import (
    DEFAULT_TARGET_MAX,
    DEFAULT_TARGET_MIN,
    FLAG_MISSING_MOMENTUM,
    FLAG_MISSING_VALUE,
    compute_composite_score,
    derive_quality_flags,
    select_screened,
)


# ---------- 헬퍼 ----------


def _c(symbol: str) -> Constituent:
    return Constituent(
        symbol=symbol,
        company_name=f"{symbol} Inc.",
        sector="Tech",
        sub_sector="SW",
        date_added=date(2010, 1, 1),
    )


def _clean(symbol: str, z: float, **kw) -> tuple[Constituent, FactorScores]:
    return _c(symbol), FactorScores(momentum_z=z, value_z=z, **kw)


def _flagged_value(symbol: str, momentum_z: float) -> tuple[Constituent, FactorScores]:
    """momentum_z 있고 value_z 없음 → missing_value 만 플래그."""
    return _c(symbol), FactorScores(momentum_z=momentum_z)


def _fully_flagged(symbol: str) -> tuple[Constituent, FactorScores]:
    """momentum_z, value_z 모두 None → 플래그 둘 다."""
    return _c(symbol), FactorScores()


# ---------- compute_composite_score ----------


def test_composite_with_both_z():
    assert compute_composite_score(1.0, 0.5) == pytest.approx(0.75)


def test_composite_substitutes_none_with_zero():
    # momentum None → 0, value 1.0 → 0.5*1.0 = 0.5
    assert compute_composite_score(None, 1.0) == pytest.approx(0.5)
    assert compute_composite_score(1.0, None) == pytest.approx(0.5)
    assert compute_composite_score(None, None) == 0.0


def test_composite_respects_custom_weights():
    assert compute_composite_score(1.0, 0.0, momentum_weight=0.7, value_weight=0.3) == pytest.approx(0.7)


# ---------- derive_quality_flags ----------


def test_flags_empty_when_both_z_present():
    fs = FactorScores(momentum_z=1.0, value_z=0.5)
    assert derive_quality_flags(fs) == []


def test_flags_when_only_momentum_missing():
    fs = FactorScores(value_z=0.5)
    assert derive_quality_flags(fs) == [FLAG_MISSING_MOMENTUM]


def test_flags_when_only_value_missing():
    fs = FactorScores(momentum_z=1.0)
    assert derive_quality_flags(fs) == [FLAG_MISSING_VALUE]


def test_flags_when_both_missing():
    assert derive_quality_flags(FactorScores()) == [FLAG_MISSING_MOMENTUM, FLAG_MISSING_VALUE]


# ---------- select_screened: 기본 ----------


def test_select_raises_when_input_below_target_min():
    items = [_clean(f"S{i}", 1.0) for i in range(DEFAULT_TARGET_MIN - 1)]
    with pytest.raises(ValueError, match="target_min"):
        select_screened(items)


def test_select_returns_all_clean_when_exactly_target_min():
    items = [_clean(f"S{i:02d}", 1.0 - i * 0.01) for i in range(DEFAULT_TARGET_MIN)]
    result = select_screened(items)
    assert len(result) == DEFAULT_TARGET_MIN
    assert [s.rank for s in result] == list(range(1, DEFAULT_TARGET_MIN + 1))


def test_select_caps_at_target_max():
    items = [_clean(f"S{i:02d}", 1.0 - i * 0.01) for i in range(30)]
    result = select_screened(items)
    assert len(result) == DEFAULT_TARGET_MAX


def test_select_returns_clean_count_when_between_min_and_max():
    # 18 clean → 결과는 18개 (15~20 범위 안)
    items = [_clean(f"S{i:02d}", 1.0 - i * 0.01) for i in range(18)]
    result = select_screened(items)
    assert len(result) == 18


# ---------- select_screened: rank 와 정렬 ----------


def test_ranks_assigned_by_composite_descending():
    # 점수가 무작위로 섞인 입력
    raw_scores = [0.3, 1.0, -0.2, 0.5, 0.8, 0.1, 0.9, 0.6, 0.4, 0.7,
                  0.2, 0.05, 0.45, 0.55, 0.75]
    items = [_clean(f"S{i:02d}", s) for i, s in enumerate(raw_scores)]
    result = select_screened(items)

    # rank 1 이 최고 점수, rank 15 가 최저
    assert result[0].composite_score == pytest.approx(1.0)
    assert result[-1].composite_score == pytest.approx(-0.2)
    # 모든 인접 페어는 내림차순
    for prev, curr in zip(result, result[1:]):
        assert prev.composite_score >= curr.composite_score


def test_tiebreaker_uses_momentum_12_1m():
    # 두 종목이 동일 composite. momentum_12_1m 가 큰 쪽이 위 rank
    items = [_clean(f"PAD{i:02d}", 0.5 - i * 0.01) for i in range(13)]
    items.append(_clean("HIGH", 1.0, momentum_12_1m=0.30))
    items.append(_clean("LOW", 1.0, momentum_12_1m=0.10))
    result = select_screened(items)
    # 점수 1.0 동점 → momentum_12_1m 큰 HIGH 가 rank 1
    assert result[0].symbol == "HIGH"
    assert result[1].symbol == "LOW"


def test_tiebreaker_falls_through_to_symbol():
    # composite, momentum_12_1m, fcf_yield 모두 동일 → symbol 알파벳
    items = [_clean(f"PAD{i:02d}", 0.5 - i * 0.01) for i in range(13)]
    items.append(_clean("ZZZ", 1.0, momentum_12_1m=0.20, fcf_yield=0.05))
    items.append(_clean("AAA", 1.0, momentum_12_1m=0.20, fcf_yield=0.05))
    result = select_screened(items)
    # 알파벳 ASC → AAA 가 먼저
    assert result[0].symbol == "AAA"
    assert result[1].symbol == "ZZZ"


# ---------- select_screened: 클린 우선 정책 ----------


def test_clean_preferred_over_higher_score_flagged():
    """flagged 가 score 가 더 높아도 후보 풀(top target_max) 안의 clean 이 우선.

    이게 docs §3.5 의 핵심 정책 — 'data_quality_flags 가 비어있는 종목부터 채워'.
    구체적으로: 후보 풀이 5 flagged + 15 clean 이면 → 15 clean 만 선택.
    """
    # 5 flagged 가 최고 점수 3.5~3.1, 15 clean 이 3.0~1.6 → top 20 후보는 정확히 이들
    high_flagged = [_flagged_value(f"F{i}", 3.5 - i * 0.1) for i in range(5)]
    clean = [_clean(f"C{i:02d}", 3.0 - i * 0.1) for i in range(15)]
    result = select_screened(high_flagged + clean)

    assert len(result) == 15
    assert all(not s.data_quality_flags for s in result)
    # 최고 점수 flagged 도 결과에 없음 — 후보 풀 안의 clean 이 우선
    assert {s.symbol for s in result}.isdisjoint({f"F{i}" for i in range(5)})


def test_flagged_fills_when_clean_below_target_min():
    # 10 clean + 10 flagged → 10 clean + 5 flagged = 15
    clean = [_clean(f"C{i:02d}", 1.0 - i * 0.05) for i in range(10)]
    flagged = [_flagged_value(f"F{i:02d}", 0.4 - i * 0.05) for i in range(10)]
    result = select_screened(clean + flagged)

    assert len(result) == DEFAULT_TARGET_MIN
    n_clean = sum(1 for s in result if not s.data_quality_flags)
    n_flagged = sum(1 for s in result if s.data_quality_flags)
    assert n_clean == 10
    assert n_flagged == 5


def test_flagged_rank_higher_than_clean_when_score_higher():
    """selected 에 들어간 flagged 의 점수가 clean 보다 높으면 rank 도 위."""
    # 10 clean (점수 0.5~) + flagged 1개 (점수 1.0) + 4 padding clean
    clean_low = [_clean(f"C{i:02d}", 0.5 - i * 0.01) for i in range(10)]
    flag_high = [_flagged_value("HIGHFLAG", 5.0)]   # score = 0.5 * 5.0 = 2.5
    pad = [_clean(f"P{i}", -1.0 - i * 0.01) for i in range(4)]  # 보강

    result = select_screened(clean_low + flag_high + pad)
    # clean 만 14개라 target_min 부족 → flagged 1개 채움
    assert any(s.symbol == "HIGHFLAG" for s in result)
    # HIGHFLAG 점수 = 2.5 → 가장 높음 → rank 1
    assert result[0].symbol == "HIGHFLAG"
    assert FLAG_MISSING_VALUE in result[0].data_quality_flags


def test_all_flagged_when_no_clean_available():
    items = [_fully_flagged(f"F{i:02d}") for i in range(20)]
    result = select_screened(items)
    assert len(result) == DEFAULT_TARGET_MIN
    assert all(set(s.data_quality_flags) == {FLAG_MISSING_MOMENTUM, FLAG_MISSING_VALUE} for s in result)


# ---------- select_screened: ScreenedStock 필드 검증 ----------


def test_screened_stock_carries_constituent_metadata():
    items = [_clean(f"S{i:02d}", 1.0 - i * 0.01) for i in range(15)]
    result = select_screened(items)
    top = result[0]
    assert top.symbol == "S00"
    assert top.company_name == "S00 Inc."
    assert top.sector == "Tech"
    assert top.sub_sector == "SW"


def test_screened_stock_peer_context_left_empty():
    """peer_context 는 peer_context.py 가 채움 — score.py 는 비워둠."""
    items = [_clean(f"S{i:02d}", 1.0 - i * 0.01) for i in range(15)]
    result = select_screened(items)
    assert all(s.peer_context == [] for s in result)


def test_screened_stock_carries_factors():
    items = [_clean(f"S{i:02d}", 1.0 - i * 0.01, momentum_12_1m=0.2) for i in range(15)]
    result = select_screened(items)
    assert result[0].factors.momentum_12_1m == 0.2
