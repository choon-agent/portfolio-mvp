"""스크리닝 4단계 — 결합 점수, 랭킹, 상위 선택.

설계 근거: docs/01-screening.md §3.4~3.5

순수 함수 — 네트워크/S3/AWS/LLM 호출 없음.

이 모듈의 책임:
1. composite_score = w_m × momentum_z + w_v × value_z
2. None z-score 는 0(중립) 으로 대체 (docs §2.3) — data_quality_flags 에 명시
3. composite 내림차순 정렬 (동점은 momentum_12_1m → fcf_yield → symbol)
4. 상위 target_max 후보 → flags 비어있는 종목 우선 → target_min~target_max 확정
5. 최종 selected 에 composite 내림차순으로 rank 부여 (1 이 최상위)

선택과 랭크의 분리:
- 선택은 "클린 우선" 정책 — 같은 후보 풀 안에서 flagged 보다 clean 을 선호
- 랭크는 "composite 우선" — 일단 selected 에 들어간 후에는 composite 점수만이 순서 결정
  → flagged 가 selected 에 포함되었다면, 그 점수가 selected 내 다른 clean 보다 높을 때
     해당 clean 보다 위 rank 를 차지할 수 있음

ScreenedStock.peer_context 는 빈 리스트로 두고 — peer_context.py 가 채움.
"""
from __future__ import annotations

from dataclasses import dataclass

from common.models import Constituent
from screening.schemas import FactorScores, ScreenedStock

# ---------- 기본 파라미터 (docs/01-screening.md §3.4) ----------

DEFAULT_W_MOMENTUM = 0.5
DEFAULT_W_VALUE = 0.5
DEFAULT_TARGET_MIN = 15  # CHARTER §3.2 포지션 수 하한 + ScreeningResult.selected min_length
DEFAULT_TARGET_MAX = 20  # ScreeningResult.selected max_length

# 데이터 품질 플래그 (관측·디버깅 용 — 점수 계산 자체에는 영향 없음)
FLAG_MISSING_MOMENTUM = "missing_momentum"
FLAG_MISSING_VALUE = "missing_value"


# ---------- 점수 계산 ----------


def compute_composite_score(
    momentum_z: float | None,
    value_z: float | None,
    *,
    momentum_weight: float = DEFAULT_W_MOMENTUM,
    value_weight: float = DEFAULT_W_VALUE,
) -> float:
    """w_m × momentum_z + w_v × value_z. None z-score 는 0(중립)."""
    m = momentum_z if momentum_z is not None else 0.0
    v = value_z if value_z is not None else 0.0
    return momentum_weight * m + value_weight * v


def derive_quality_flags(factors: FactorScores) -> list[str]:
    """z-score 가 None 인 차원에 대한 플래그.

    raw 값(예: pe_ttm)이 None 이어도 z-score 는 다른 컴포넌트로 채워질 수 있으므로
    최종 z-score 기준으로 판단.
    """
    flags: list[str] = []
    if factors.momentum_z is None:
        flags.append(FLAG_MISSING_MOMENTUM)
    if factors.value_z is None:
        flags.append(FLAG_MISSING_VALUE)
    return flags


# ---------- 정렬·선택 (내부) ----------


@dataclass(frozen=True, slots=True)
class _Record:
    constituent: Constituent
    factors: FactorScores
    composite: float
    flags: tuple[str, ...]


def _sort_key(r: _Record) -> tuple[float, float, float, str]:
    """composite desc → momentum_12_1m desc → fcf_yield desc → symbol asc.

    ascending 정렬 기준이므로 desc 필드는 부호 반전. None tiebreaker 는 +inf 로
    항상 마지막에 위치.
    """
    m12 = r.factors.momentum_12_1m
    fcf = r.factors.fcf_yield
    return (
        -r.composite,
        -m12 if m12 is not None else float("inf"),
        -fcf if fcf is not None else float("inf"),
        r.constituent.symbol,
    )


def _to_screened_stock(record: _Record, rank: int) -> ScreenedStock:
    return ScreenedStock(
        symbol=record.constituent.symbol,
        company_name=record.constituent.company_name,
        sector=record.constituent.sector,
        sub_sector=record.constituent.sub_sector,
        rank=rank,
        composite_score=record.composite,
        factors=record.factors,
        data_quality_flags=list(record.flags),
        # peer_context 는 default_factory=list — peer_context.py 가 채움
    )


# ---------- 메인 ----------


def select_screened(
    items: list[tuple[Constituent, FactorScores]],
    *,
    momentum_weight: float = DEFAULT_W_MOMENTUM,
    value_weight: float = DEFAULT_W_VALUE,
    target_min: int = DEFAULT_TARGET_MIN,
    target_max: int = DEFAULT_TARGET_MAX,
) -> list[ScreenedStock]:
    """입력 종목에 점수·플래그·랭크 부여 후 상위 target_min~target_max 선택.

    알고리즘:
      1. 각 종목 composite_score 계산, flags 도출
      2. composite desc + 동점 처리로 전체 정렬
      3. 상위 target_max 개 후보
      4. 후보 중 clean(flags 비어 있음) 우선 → 부족하면 flagged 로 채워 target_min 충족
      5. 선택된 종목을 composite 기준으로 재정렬 → rank 1..N

    제약: len(items) >= target_min 이어야 함.
    """
    if len(items) < target_min:
        raise ValueError(
            f"items({len(items)}) 가 target_min({target_min}) 미만 — 스크리닝 입력 부족"
        )

    records = [
        _Record(
            constituent=c,
            factors=fs,
            composite=compute_composite_score(
                fs.momentum_z,
                fs.value_z,
                momentum_weight=momentum_weight,
                value_weight=value_weight,
            ),
            flags=tuple(derive_quality_flags(fs)),
        )
        for c, fs in items
    ]

    sorted_all = sorted(records, key=_sort_key)
    candidates = sorted_all[:target_max]

    # 클린 우선 채우기
    clean = [r for r in candidates if not r.flags]
    flagged = [r for r in candidates if r.flags]
    target_n = max(target_min, min(target_max, len(clean)))
    target_n = min(target_n, len(candidates))  # 후보 수에 의한 상한

    selected = list(clean[:target_n])
    if len(selected) < target_n:
        selected.extend(flagged[: target_n - len(selected)])

    # composite 기준 재정렬 + rank 부여 (flagged 가 더 높은 점수면 clean 보다 앞 rank)
    final = sorted(selected, key=_sort_key)
    return [_to_screened_stock(r, rank=i + 1) for i, r in enumerate(final)]
