"""스크리닝 5단계 — 선정 종목에 peer_context 부착.

설계 근거: docs/01-screening.md §2.2, §3.6, 부록 A

순수 함수 — 네트워크/S3/AWS/LLM 호출 없음.

이 모듈의 책임:
- 선정된 각 ScreenedStock 에 같은 sub_sector(우선) → 같은 sector(폴백) 의 상위 N개
  peer 의 멀티플(P/E·EV/EBITDA·FCF yield)을 PeerComparable 리스트로 부착
- Bull/Bear 단계 입력 토큰 절감 + 동일 시점 데이터 일관성 보장
  (docs/02-bull-bear.md §2.1 의 peer_context 공급 책임)

피어 검색 폴백 체인:
  1. 같은 sub_sector 내 — 가장 정합성 높은 비교
  2. 부족하면 같은 sector 의 다른 sub_sector 로 확장 — singleton sub_sector
     (예: 5종목 dry-run 에서 발견된 "Banks - Regional" 1종목, "Oil & Gas Refining
     & Marketing" 1종목) 처리
  3. 그래도 없으면 빈 리스트 — sector 정보 자체가 없거나 매칭 0인 경우
docs §10 "peer_context 범위" 미해결 항목은 2026-04-28 30종목 dry-run 결과 (단일-원소
sub_sector 가 운영에서 빈번할 것으로 예측) 에 따라 sector 폴백 도입으로 해결.

설계 결정:
- 피어 풀: 유니버스 필터 통과 종목 전체 (selected 15~20 보다 넓은 ~478)
- 피어 정렬: composite_score 내림차순, 동점은 symbol asc
- sub_sector peer 가 sector peer 앞에 배치 — 같은 sub_sector 가 더 정합성 높으므로
- sub_sector 또는 sector 가 None 인 pool 항목은 그룹화에서 제외 (그룹 키 없음)
- selected 의 sub_sector 가 None 이어도 sector 가 있으면 sector 폴백 사용
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


def _collect_peers(
    stock: ScreenedStock,
    by_sub: dict[str, list[tuple[Constituent, FactorScores, float]]],
    by_sector: dict[str, list[tuple[Constituent, FactorScores, float]]],
    n_peers: int,
) -> list[PeerComparable]:
    """sub_sector 우선, 부족하면 sector 폴백."""
    sub_peers: list[tuple[Constituent, FactorScores]] = []
    if stock.sub_sector is not None:
        sub_peers = [
            (c, fs)
            for c, fs, _ in by_sub.get(stock.sub_sector, [])
            if c.symbol != stock.symbol
        ]

    if len(sub_peers) >= n_peers:
        return [_to_peer(c, fs) for c, fs in sub_peers[:n_peers]]

    # Sector 폴백 — sub_sector peer 유지 + 다른 sub_sector 에서 보충
    sub_symbols = {c.symbol for c, _ in sub_peers}
    sector_extras: list[tuple[Constituent, FactorScores]] = []
    if stock.sector is not None:
        sector_extras = [
            (c, fs)
            for c, fs, _ in by_sector.get(stock.sector, [])
            if c.symbol != stock.symbol and c.symbol not in sub_symbols
        ]

    needed = n_peers - len(sub_peers)
    combined = sub_peers + sector_extras[:needed]
    return [_to_peer(c, fs) for c, fs in combined]


def attach_peer_context(
    selected: list[ScreenedStock],
    pool: list[tuple[Constituent, FactorScores]],
    *,
    n_peers: int = DEFAULT_N_PEERS,
    momentum_weight: float = DEFAULT_W_MOMENTUM,
    value_weight: float = DEFAULT_W_VALUE,
) -> list[ScreenedStock]:
    """각 selected 에 sub_sector(우선) → sector(폴백) 상위 n_peers PeerComparable 부착.

    pool 은 보통 유니버스 필터 통과 전체 (selected 보다 넓어야 비교가 의미 있음).
    pool 에 selected 자신이 포함돼 있어도 자기 자신은 제외하고 피어 선정.

    동점 처리: composite desc → symbol asc (재현성).
    sub_sector peer 가 sector 폴백 peer 앞에 배치 (정합성 우선).

    반환: 새 ScreenedStock 리스트 (model_copy) — 입력 selected 는 변경하지 않음.
    """
    if n_peers > DEFAULT_N_PEERS:
        raise ValueError(
            f"n_peers ({n_peers}) > ScreenedStock.peer_context max_length ({DEFAULT_N_PEERS})"
        )

    # 1. pool 을 sub_sector 별 + sector 별로 묶고 composite 계산
    by_sub: dict[str, list[tuple[Constituent, FactorScores, float]]] = {}
    by_sector: dict[str, list[tuple[Constituent, FactorScores, float]]] = {}

    for constituent, factors in pool:
        composite = compute_composite_score(
            factors.momentum_z,
            factors.value_z,
            momentum_weight=momentum_weight,
            value_weight=value_weight,
        )
        if constituent.sub_sector is not None:
            by_sub.setdefault(constituent.sub_sector, []).append(
                (constituent, factors, composite)
            )
        if constituent.sector is not None:
            by_sector.setdefault(constituent.sector, []).append(
                (constituent, factors, composite)
            )

    # 2. 그룹별 composite desc + symbol asc 정렬
    def _sort_key(t: tuple[Constituent, FactorScores, float]) -> tuple[float, str]:
        return (-t[2], t[0].symbol)

    for members in by_sub.values():
        members.sort(key=_sort_key)
    for members in by_sector.values():
        members.sort(key=_sort_key)

    # 3. 각 selected 에 peer_context 부착
    out: list[ScreenedStock] = []
    for stock in selected:
        peers = _collect_peers(stock, by_sub, by_sector, n_peers)
        out.append(stock.model_copy(update={"peer_context": peers}))

    return out
