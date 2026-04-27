"""스크리닝 3단계 — 단면(cross-section) 단위 정규화.

설계 근거: docs/01-screening.md §3.2 결합, §3.3 섹터 z-score

순수 함수 — 네트워크/S3/AWS/LLM 호출 없음.

이 모듈의 책임:
1. 종목별 raw 모멘텀 두 가지를 결합 (0.7 × 12_1m + 0.3 × 6m)
2. 그룹화 (sub_sector → sector → universe 3단 폴백)
3. 그룹 내 모집단 z-score 계산 (모멘텀 결합값, 밸류 세 컴포넌트 각각)
4. 밸류는 P/E·EV/EBITDA 의 z-score 부호 반전 후 세 컴포넌트 평균 → value_z

설계 결정:
- 결합 모멘텀은 두 raw 값이 모두 있어야 산출 (한쪽만 있으면 None — 가중치 일관성 보장)
- 밸류는 사용 가능한 컴포넌트만 평균 (1~3개)
- raw 가 None 인 종목은 해당 z-score 도 None — score.py 가 0(중립)으로 대체
- 그룹 표본 < 2 또는 std=0 이면 z-score 모두 None (또는 0)
"""
from __future__ import annotations

import statistics
from collections.abc import Callable, Iterable
from typing import TypeVar

from common.models import Constituent
from screening.schemas import FactorScores

# ---------- 기본 파라미터 (docs/01-screening.md §3.2~3.3) ----------

DEFAULT_W_MOMENTUM_12_1M = 0.7
DEFAULT_W_MOMENTUM_6M = 0.3
DEFAULT_MIN_GROUP_SIZE = 5  # sub_sector 표본 < 5 → sector 폴백

# 그룹 키 prefix (관측·디버깅 시 어느 폴백 단계에서 그룹된지 식별)
GROUP_PREFIX_SECTOR = "_sector:"
GROUP_KEY_UNIVERSE = "_universe"


T = TypeVar("T")
Item = tuple[Constituent, FactorScores]


# ---------- 결합 ----------


def combined_momentum(
    factors: FactorScores,
    *,
    w_12_1m: float = DEFAULT_W_MOMENTUM_12_1M,
    w_6m: float = DEFAULT_W_MOMENTUM_6M,
) -> float | None:
    """0.7 × momentum_12_1m + 0.3 × momentum_6m.

    두 raw 값 모두 있어야 산출. 한쪽이라도 None 이면 None — 부분 데이터 종목에
    다른 가중치를 적용하면 단면 내 비교 일관성이 깨지므로.
    """
    if factors.momentum_12_1m is None or factors.momentum_6m is None:
        return None
    return w_12_1m * factors.momentum_12_1m + w_6m * factors.momentum_6m


def _value_z_from_components(
    z_pe: float | None,
    z_ev_ebitda: float | None,
    z_fcf_yield: float | None,
) -> float | None:
    """세 컴포넌트 z-score 의 부호 정규화 후 평균.

    P/E·EV/EBITDA: 낮을수록 좋음 → z-score 부호 반전
    FCF yield: 높을수록 좋음 → 그대로

    사용 가능한 컴포넌트만 평균 (1~3개). 모두 None 이면 None.
    """
    components: list[float] = []
    if z_pe is not None:
        components.append(-z_pe)
    if z_ev_ebitda is not None:
        components.append(-z_ev_ebitda)
    if z_fcf_yield is not None:
        components.append(z_fcf_yield)
    if not components:
        return None
    return sum(components) / len(components)


# ---------- z-score ----------


def z_scores(values: list[float | None]) -> list[float | None]:
    """그룹 내 모집단 z-score.

    유효 값(None 아닌 값) 만으로 mean/pstdev 계산 후 변환.
    None 입력은 None 출력. 유효 값이 2개 미만이거나 std=0 이면 모두 None.
    """
    valid = [v for v in values if v is not None]
    if len(valid) < 2:
        return [None] * len(values)
    mean = statistics.mean(valid)
    std = statistics.pstdev(valid)
    if std == 0:
        return [None] * len(values)
    return [(v - mean) / std if v is not None else None for v in values]


# ---------- 그룹화 (3단 폴백) ----------


def _bucket(
    items: Iterable[T],
    key_fn: Callable[[T], str | None],
    min_size: int,
) -> tuple[dict[str, list[T]], list[T]]:
    """key_fn 으로 그룹화. min_size 미만 그룹과 키가 None 인 항목은 remaining 으로.

    반환: (충족된_그룹들, 다음_단계로_넘길_나머지).
    """
    by_key: dict[str, list[T]] = {}
    no_key: list[T] = []
    for item in items:
        k = key_fn(item)
        if k is None:
            no_key.append(item)
        else:
            by_key.setdefault(k, []).append(item)

    finalized: dict[str, list[T]] = {}
    remaining: list[T] = list(no_key)
    for k, members in by_key.items():
        if len(members) >= min_size:
            finalized[k] = members
        else:
            remaining.extend(members)
    return finalized, remaining


def group_with_fallback(
    items: list[Item],
    min_size: int = DEFAULT_MIN_GROUP_SIZE,
) -> dict[str, list[Item]]:
    """sub_sector → sector → universe 순으로 폴백하며 그룹화.

    반환 dict 의 키:
      - sub_sector 명: 1차 그룹 (표본 충분)
      - "_sector:<name>": sector 폴백 그룹
      - "_universe": sector 도 부족했던 항목들의 최종 폴백
    """
    final: dict[str, list[Item]] = {}

    sub_groups, remaining = _bucket(items, lambda x: x[0].sub_sector, min_size)
    final.update(sub_groups)

    sector_groups, remaining = _bucket(remaining, lambda x: x[0].sector, min_size)
    for k, v in sector_groups.items():
        final[f"{GROUP_PREFIX_SECTOR}{k}"] = v

    if remaining:
        final[GROUP_KEY_UNIVERSE] = remaining

    return final


# ---------- 메인 ----------


def normalize_factor_scores(
    items: list[Item],
    *,
    w_12_1m: float = DEFAULT_W_MOMENTUM_12_1M,
    w_6m: float = DEFAULT_W_MOMENTUM_6M,
    min_group_size: int = DEFAULT_MIN_GROUP_SIZE,
) -> dict[str, FactorScores]:
    """모든 종목의 FactorScores 에 momentum_z, value_z 채워서 반환.

    각 그룹 내에서:
      1. raw 모멘텀 결합값과 raw 밸류 세 컴포넌트를 따로 z-score
      2. 밸류는 부호 정규화 후 평균 → value_z

    raw 가 None 인 종목은 해당 z-score 도 None.
    그룹 표본 부족(<2)이나 std=0 시 그룹 전체 z-score 가 None.

    반환된 FactorScores 는 새 인스턴스 (model_copy) — 입력은 변경하지 않음.
    """
    groups = group_with_fallback(items, min_group_size)
    output: dict[str, FactorScores] = {}

    for members in groups.values():
        mom_raw = [combined_momentum(fs, w_12_1m=w_12_1m, w_6m=w_6m) for _, fs in members]
        pe_raw = [fs.pe_ttm for _, fs in members]
        ev_raw = [fs.ev_ebitda for _, fs in members]
        fcf_raw = [fs.fcf_yield for _, fs in members]

        mom_z_list = z_scores(mom_raw)
        pe_z_list = z_scores(pe_raw)
        ev_z_list = z_scores(ev_raw)
        fcf_z_list = z_scores(fcf_raw)

        for i, (constituent, factors) in enumerate(members):
            value_z = _value_z_from_components(
                pe_z_list[i],
                ev_z_list[i],
                fcf_z_list[i],
            )
            output[constituent.symbol] = factors.model_copy(
                update={
                    "momentum_z": mom_z_list[i],
                    "value_z": value_z,
                }
            )

    return output
