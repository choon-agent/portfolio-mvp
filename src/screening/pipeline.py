"""스크리닝 파이프라인 — 5개 모듈을 합성해 ScreeningResult 1개 반환.

설계 근거: docs/01-screening.md §3.6, §4.3, §9

순수 함수 — 네트워크/S3/AWS/LLM 호출 없음.
I/O 와 로깅은 Lambda 핸들러(src/lambdas/run_screening/) 책임.

데이터 흐름 (docs §3.6):

  constituents (~500)
    │
    ▼ universe.filter_universe (유니버스 단위)
  filter_result.passed (~478)
    │
    ▼ factors.compute_factor_scores (종목별)
  raw_factors: dict[symbol, FactorScores]  (z-score 미설정)
    │
    ▼ normalize.normalize_factor_scores (단면 단위)
  z_factors: dict[symbol, FactorScores]    (momentum_z, value_z 채워짐)
    │
    ▼ score.select_screened (단면 단위)
  selected: list[ScreenedStock]            (15~20, 랭크·플래그 부여, peer_context 빈 상태)
    │
    ▼ peer_context.attach_peer_context (선정 종목별)
  selected_with_peers
    │
    ▼ ScreeningResult 조립
  결과
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pyarrow as pa

from common.models import Constituent
from screening.factors import compute_factor_scores
from screening.normalize import normalize_factor_scores
from screening.peer_context import DEFAULT_N_PEERS, attach_peer_context
from screening.schemas import FactorScores, ScreeningResult
from screening.score import (
    DEFAULT_TARGET_MAX,
    DEFAULT_TARGET_MIN,
    DEFAULT_W_MOMENTUM,
    DEFAULT_W_VALUE,
    select_screened,
)
from screening.universe import filter_universe


def _default_run_id(as_of_date: date) -> str:
    """as_of_date 기준 ISO 타임스탬프. Lambda 가 명시적으로 지정하지 않을 때 사용."""
    return f"{as_of_date.isoformat()}T00:00:00Z"


def run_screening(
    *,
    constituents: list[Constituent],
    market_caps: dict[str, float | None],
    price_histories: dict[str, pa.Table | None],
    key_metrics_ttm: dict[str, dict[str, Any] | None],
    as_of_date: date,
    run_id: str | None = None,
    momentum_weight: float = DEFAULT_W_MOMENTUM,
    value_weight: float = DEFAULT_W_VALUE,
    target_min: int = DEFAULT_TARGET_MIN,
    target_max: int = DEFAULT_TARGET_MAX,
    n_peers: int = DEFAULT_N_PEERS,
) -> ScreeningResult:
    """스크리닝 파이프라인 실행.

    인자:
      constituents: 현재 + 과거 S&P 500 구성종목 (universe 단계가 is_current 로 필터)
      market_caps: symbol -> 시총 (None 가능). FMP quote 또는 key_metrics_ttm.marketCap 에서
                   조립 가능 — 호출 측이 결정.
      price_histories: symbol -> OHLCV pa.Table (None 가능)
      key_metrics_ttm: symbol -> FMP key-metrics-ttm 응답 dict (None 가능). 밸류 세 컴포넌트
                      (P/E TTM, EV/EBITDA TTM, FCF Yield TTM) 를 단일 엔드포인트에서 도출.
      as_of_date: 리밸런싱 기준일 (모멘텀 lookback 기준점)
      run_id: 재현용 식별자. None 이면 as_of_date 기반 ISO 타임스탬프
      momentum_weight, value_weight: composite_score 결합 가중치 (score, peer_context 공통)
      target_min, target_max: selected 길이 하한/상한
      n_peers: 종목당 peer_context 최대 개수

    반환: ScreeningResult (Pydantic 검증 통과 보장)

    예외:
      score.select_screened 가 ValueError — universe 필터 통과 종목이 target_min 미만일 때
      ScreeningResult 검증 — selected 길이/랭크/정렬이 schemas.py 제약 위반 시
    """
    # 1. 유니버스 필터
    filter_result = filter_universe(
        constituents,
        market_caps,
        price_histories,
        as_of_date,
    )

    # 2. 종목별 raw 팩터값 계산
    raw_factors: dict[str, FactorScores] = {
        c.symbol: compute_factor_scores(
            price_histories.get(c.symbol),
            key_metrics_ttm.get(c.symbol),
            as_of_date,
        )
        for c in filter_result.passed
    }

    # 3. 단면 단위 정규화 (sub_sector → sector → universe 폴백)
    items_raw = [(c, raw_factors[c.symbol]) for c in filter_result.passed]
    z_factors = normalize_factor_scores(items_raw)

    # 4. 점수·랭킹·선택
    items_z = [(c, z_factors[c.symbol]) for c in filter_result.passed]
    selected = select_screened(
        items_z,
        momentum_weight=momentum_weight,
        value_weight=value_weight,
        target_min=target_min,
        target_max=target_max,
    )

    # 5. peer_context 부착 (pool = 유니버스 통과 전체)
    selected_with_peers = attach_peer_context(
        selected,
        items_z,
        n_peers=n_peers,
        momentum_weight=momentum_weight,
        value_weight=value_weight,
    )

    # 6. ScreeningResult 조립 (Pydantic 검증)
    return ScreeningResult(
        as_of_date=as_of_date,
        universe_size=len(filter_result.passed),
        selected=selected_with_peers,
        factor_weights={"momentum": momentum_weight, "value": value_weight},
        run_id=run_id or _default_run_id(as_of_date),
    )
