"""정규화 단위 테스트.

순수 함수만 — 네트워크/S3/AWS 호출 없음.
"""
from __future__ import annotations

import statistics
from datetime import date

import pytest

from common.models import Constituent
from screening.normalize import (
    GROUP_KEY_UNIVERSE,
    GROUP_PREFIX_SECTOR,
    combined_momentum,
    group_with_fallback,
    normalize_factor_scores,
    z_scores,
)
from screening.schemas import FactorScores


# ---------- 헬퍼 ----------


def _c(symbol: str, sector: str | None = "Tech", sub_sector: str | None = "SW") -> Constituent:
    return Constituent(
        symbol=symbol,
        sector=sector,
        sub_sector=sub_sector,
        date_added=date(2010, 1, 1),
    )


def _fs(
    momentum_12_1m: float | None = None,
    momentum_6m: float | None = None,
    pe_ttm: float | None = None,
    ev_ebitda: float | None = None,
    fcf_yield: float | None = None,
) -> FactorScores:
    return FactorScores(
        momentum_12_1m=momentum_12_1m,
        momentum_6m=momentum_6m,
        pe_ttm=pe_ttm,
        ev_ebitda=ev_ebitda,
        fcf_yield=fcf_yield,
    )


# ---------- combined_momentum ----------


def test_combined_momentum_with_both_components():
    fs = _fs(momentum_12_1m=0.20, momentum_6m=0.10)
    # 0.7 * 0.20 + 0.3 * 0.10 = 0.17
    assert combined_momentum(fs) == pytest.approx(0.17)


def test_combined_momentum_returns_none_if_12_1m_missing():
    assert combined_momentum(_fs(momentum_6m=0.10)) is None


def test_combined_momentum_returns_none_if_6m_missing():
    assert combined_momentum(_fs(momentum_12_1m=0.20)) is None


def test_combined_momentum_returns_none_if_both_missing():
    assert combined_momentum(_fs()) is None


def test_combined_momentum_respects_custom_weights():
    fs = _fs(momentum_12_1m=1.0, momentum_6m=0.0)
    assert combined_momentum(fs, w_12_1m=0.5, w_6m=0.5) == pytest.approx(0.5)


# ---------- z_scores ----------


def test_z_scores_empty_list():
    assert z_scores([]) == []


def test_z_scores_all_none():
    assert z_scores([None, None, None]) == [None, None, None]


def test_z_scores_under_two_valid_returns_all_none():
    assert z_scores([None, 5.0, None]) == [None, None, None]


def test_z_scores_constant_values_returns_all_none():
    """std=0 → z-score 정의 불가 → 모두 None."""
    assert z_scores([3.0, 3.0, 3.0]) == [None, None, None]


def test_z_scores_known_distribution():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = z_scores(values)
    mean = 3.0
    std = statistics.pstdev(values)  # sqrt(2) ≈ 1.4142
    expected = [(v - mean) / std for v in values]
    for got, exp in zip(result, expected):
        assert got == pytest.approx(exp)


def test_z_scores_preserves_none_positions_with_valid_others():
    result = z_scores([1.0, None, 3.0, 5.0])
    valid = [1.0, 3.0, 5.0]
    mean = statistics.mean(valid)
    std = statistics.pstdev(valid)
    assert result[0] == pytest.approx((1.0 - mean) / std)
    assert result[1] is None
    assert result[2] == pytest.approx((3.0 - mean) / std)
    assert result[3] == pytest.approx((5.0 - mean) / std)


def test_z_scores_sum_of_valid_is_zero():
    """z-score 의 합 = 0 (mean 정의에 의해)."""
    result = z_scores([1.0, 2.0, 3.0, 4.0, 5.0])
    assert sum(result) == pytest.approx(0.0)  # type: ignore[arg-type]


# ---------- group_with_fallback ----------


def test_group_by_sub_sector_when_sufficient():
    items = [(_c(f"S{i}", sub_sector="SW"), _fs()) for i in range(5)]
    groups = group_with_fallback(items, min_size=5)
    assert "SW" in groups
    assert len(groups["SW"]) == 5


def test_falls_back_to_sector_when_sub_sector_too_small():
    items = (
        [(_c(f"A{i}", sector="Tech", sub_sector="SW"), _fs()) for i in range(2)]
        + [(_c(f"B{i}", sector="Tech", sub_sector="HW"), _fs()) for i in range(3)]
    )
    groups = group_with_fallback(items, min_size=5)
    # SW(2), HW(3) 모두 부족 → Tech sector 로 합쳐 5개
    assert f"{GROUP_PREFIX_SECTOR}Tech" in groups
    assert len(groups[f"{GROUP_PREFIX_SECTOR}Tech"]) == 5


def test_falls_back_to_universe_when_sector_too_small():
    items = (
        [(_c(f"T{i}", sector="Tech", sub_sector="SW"), _fs()) for i in range(2)]
        + [(_c(f"E{i}", sector="Energy", sub_sector="OIL"), _fs()) for i in range(2)]
    )
    groups = group_with_fallback(items, min_size=5)
    # 두 sector 모두 부족 → universe 폴백에 4개
    assert GROUP_KEY_UNIVERSE in groups
    assert len(groups[GROUP_KEY_UNIVERSE]) == 4


def test_mixed_groups_some_sufficient_some_fallback():
    # SW(6): sub_sector 충족
    # API(3) + DB(2): 둘 다 부족하지만 합치면 Tech(5) 충족 → sector 폴백
    items = (
        [(_c(f"S{i}", sector="Tech", sub_sector="SW"), _fs()) for i in range(6)]
        + [(_c(f"A{i}", sector="Tech", sub_sector="API"), _fs()) for i in range(3)]
        + [(_c(f"D{i}", sector="Tech", sub_sector="DB"), _fs()) for i in range(2)]
    )
    groups = group_with_fallback(items, min_size=5)
    assert len(groups["SW"]) == 6
    assert len(groups[f"{GROUP_PREFIX_SECTOR}Tech"]) == 5


def test_handles_none_sub_sector():
    items = (
        [(_c(f"S{i}", sector="Tech", sub_sector="SW"), _fs()) for i in range(5)]
        + [(_c("UNK", sector="Tech", sub_sector=None), _fs())]
    )
    groups = group_with_fallback(items, min_size=5)
    # SW 그룹 5개, UNK 는 sub_sector 없어서 sector 단계로 → Tech 1개 (부족) → universe 폴백
    assert len(groups["SW"]) == 5
    assert GROUP_KEY_UNIVERSE in groups
    assert [c.symbol for c, _ in groups[GROUP_KEY_UNIVERSE]] == ["UNK"]


# ---------- normalize_factor_scores ----------


def test_normalize_fills_momentum_z_within_subsector():
    # 5종목, 동일 sub_sector — 모두 모멘텀 raw 값 있음
    items = []
    for i, m12 in enumerate([0.10, 0.20, 0.30, 0.40, 0.50]):
        items.append((_c(f"S{i}", sub_sector="SW"), _fs(momentum_12_1m=m12, momentum_6m=0.0)))
    output = normalize_factor_scores(items)

    # 결합 모멘텀 = 0.7 × m12 + 0.3 × 0 = 0.7 × m12
    combined = [0.7 * m for m in [0.10, 0.20, 0.30, 0.40, 0.50]]
    mean = statistics.mean(combined)
    std = statistics.pstdev(combined)
    expected_z = [(c - mean) / std for c in combined]

    for i, exp in enumerate(expected_z):
        assert output[f"S{i}"].momentum_z == pytest.approx(exp)


def test_normalize_value_z_applies_sign_flip():
    # 같은 그룹 5종목, P/E 만 다양하게 (작을수록 좋음 → z-score 부호 반전)
    pes = [10.0, 15.0, 20.0, 25.0, 30.0]
    items = [(_c(f"S{i}", sub_sector="SW"), _fs(pe_ttm=pe)) for i, pe in enumerate(pes)]
    output = normalize_factor_scores(items)

    mean = statistics.mean(pes)
    std = statistics.pstdev(pes)
    expected_pe_z = [(p - mean) / std for p in pes]
    # value_z 는 -pe_z 평균 (다른 컴포넌트 None)
    for i, pe_z in enumerate(expected_pe_z):
        assert output[f"S{i}"].value_z == pytest.approx(-pe_z)


def test_normalize_value_z_averages_three_components():
    # 모든 컴포넌트 있는 경우 — value_z 는 (-z_pe + -z_ev + z_fcf) / 3
    items = [
        (_c(f"S{i}", sub_sector="SW"),
         _fs(pe_ttm=pe, ev_ebitda=ev, fcf_yield=fcf))
        for i, (pe, ev, fcf) in enumerate(
            [(10, 5, 0.10), (20, 10, 0.05), (30, 15, 0.0), (40, 20, -0.05), (50, 25, -0.10)]
        )
    ]
    output = normalize_factor_scores(items)

    # 첫 종목은 모든 컴포넌트가 그룹 내 최저값 → -z_pe, -z_ev 양수, z_fcf 양수
    # → value_z 는 양의 값
    assert output["S0"].value_z is not None
    assert output["S0"].value_z > 0
    # 마지막 종목은 반대
    assert output["S4"].value_z is not None
    assert output["S4"].value_z < 0


def test_normalize_none_raw_yields_none_z():
    # 한 종목만 momentum 결측 → momentum_z 는 None
    items = [
        (_c("MISS", sub_sector="SW"), _fs(momentum_12_1m=None, momentum_6m=None)),
    ] + [
        (_c(f"OK{i}", sub_sector="SW"), _fs(momentum_12_1m=0.1 * i, momentum_6m=0.05 * i))
        for i in range(1, 6)
    ]
    output = normalize_factor_scores(items)
    assert output["MISS"].momentum_z is None
    # 다른 종목은 정상 z-score
    for i in range(1, 6):
        assert output[f"OK{i}"].momentum_z is not None


def test_normalize_uses_sector_fallback_for_small_subsector():
    # sub_sector 별로는 부족하지만 sector 로 합치면 충분
    items = (
        [(_c(f"A{i}", sector="Tech", sub_sector="API"),
          _fs(momentum_12_1m=0.10 * i, momentum_6m=0.05 * i))
         for i in range(3)]
        + [(_c(f"D{i}", sector="Tech", sub_sector="DB"),
            _fs(momentum_12_1m=0.20 + 0.10 * i, momentum_6m=0.05 * i))
           for i in range(3)]
    )
    output = normalize_factor_scores(items)
    # 모두 Tech sector 폴백 그룹에서 z-score
    all_z = [output[s].momentum_z for s in [f"A{i}" for i in range(3)] + [f"D{i}" for i in range(3)]]
    # 합이 0 (z-score 합 정의)
    assert sum(z for z in all_z if z is not None) == pytest.approx(0.0)


def test_normalize_does_not_mutate_input():
    items = [(_c(f"S{i}", sub_sector="SW"), _fs(momentum_12_1m=0.1 * i, momentum_6m=0.05))
             for i in range(5)]
    original_first = items[0][1].model_copy()
    normalize_factor_scores(items)
    # 입력 FactorScores 가 변경되지 않았는지
    assert items[0][1] == original_first


def test_normalize_returns_one_entry_per_input_symbol():
    items = [(_c(f"S{i}", sub_sector="SW"), _fs(momentum_12_1m=0.1, momentum_6m=0.05))
             for i in range(7)]
    output = normalize_factor_scores(items)
    assert set(output.keys()) == {f"S{i}" for i in range(7)}
