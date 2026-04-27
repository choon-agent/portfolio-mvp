"""스크리닝 5단계 — 선정 종목에 peer_context 부착.

설계 근거: docs/01-screening.md §2.2, §3.6, 부록 A

순수 함수 — 네트워크/S3/AWS/LLM 호출 없음.

이 모듈의 책임:
- 선정된 각 ScreenedStock 에 같은 sub_sector 상위 N개 peer 의 멀티플(P/E·EV/EBITDA·FCF yield)
  을 PeerComparable 리스트로 부착
- Bull/Bear 단계 입력 토큰 절감 + 동일 시점 데이터 일관성 보장
  (docs/02-bull-bear.md §2.1 의 peer_context 공급 책임)

설계 결정:
- 피어 풀: 유니버스 필터 통과 종목 전체 (selected 15~20 보다 넓은 ~478)
  → score.py 가 본 모듈의 "pool" 인자에 그대로 전달
- 피어 정렬: composite_score 내림차순 (score.py 와 동일 가중치 기본값)
- 자기 자신은 제외, 상위 N=5 (ScreenedStock.peer_context max_length 와 정합)
- sub_sector 표본 부족 시 그만큼만 (0~5)
- selected 의 sub_sector 가 None 이면 빈 peer_context
- pool 에 sub_sector 가 None 인 항목은 피어로 사용하지 않음
"""
from __future__ import annotations

from common.models import Constituent
from screening.schemas import FactorScores, PeerComparable, ScreenedStock
from screening.score import (
    DEFAULT_W_MOMENTUM,
    DEFAULT_W_VALUE,
    compute_composite_score,
)

DEFAULT_N_PEERS = 5  # ScreenedStock.peer_context max_length 와 정합 (schemas.py §2.3)


def _to_peer(constituent: Constituent, factors: FactorScores) -> PeerComparable:
    return PeerComparable(
        symbol=constituent.symbol,
        pe_ttm=factors.pe_ttm,
        ev_ebitda=factors.ev_ebitda,
        fcf_yield=factors.fcf_yield,
    )


def attach_peer_context(
    selected: list[ScreenedStock],
    pool: list[tuple[Constituent, FactorScores]],
    *,
    n_peers: int = DEFAULT_N_PEERS,
    momentum_weight: float = DEFAULT_W_MOMENTUM,
    value_weight: float = DEFAULT_W_VALUE,
) -> list[ScreenedStock]:
    """각 selected 에 같은 sub_sector 상위 n_peers 의 PeerComparable 부착.

    pool 은 보통 유니버스 필터 통과 전체 (selected 보다 넓어야 비교가 의미 있음).
    pool 에 selected 자신이 포함돼 있어도 자기 자신은 제외하고 피어 선정.

    동점 처리: composite desc → symbol asc (재현성).

    반환: 새 ScreenedStock 리스트 (model_copy) — 입력 selected 는 변경하지 않음.
    """
    # ScreenedStock.peer_context 의 max_length=5 와 정합. model_copy(update=)는 재검증을
    # 하지 않으므로 함수 진입 시점에 명시적으로 차단.
    if n_peers > DEFAULT_N_PEERS:
        raise ValueError(
            f"n_peers ({n_peers}) > ScreenedStock.peer_context max_length ({DEFAULT_N_PEERS})"
        )

    # 1. pool 을 sub_sector 별로 묶고 composite 계산
    by_sub: dict[str, list[tuple[Constituent, FactorScores, float]]] = {}
    for constituent, factors in pool:
        if constituent.sub_sector is None:
            continue
        composite = compute_composite_score(
            factors.momentum_z,
            factors.value_z,
            momentum_weight=momentum_weight,
            value_weight=value_weight,
        )
        by_sub.setdefault(constituent.sub_sector, []).append(
            (constituent, factors, composite)
        )

    # 2. 각 sub_sector 내 composite desc + symbol asc 정렬
    for members in by_sub.values():
        members.sort(key=lambda t: (-t[2], t[0].symbol))

    # 3. 각 selected 에 peer_context 부착
    out: list[ScreenedStock] = []
    for stock in selected:
        if stock.sub_sector is None:
            out.append(stock.model_copy(update={"peer_context": []}))
            continue
        candidates = by_sub.get(stock.sub_sector, [])
        peers = [
            _to_peer(constituent, factors)
            for constituent, factors, _ in candidates
            if constituent.symbol != stock.symbol
        ][:n_peers]
        out.append(stock.model_copy(update={"peer_context": peers}))

    return out
