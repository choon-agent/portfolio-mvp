"""screened_to_context 매퍼 단위 테스트.

설계 근거: docs/02-bull-bear.md §2.1, 부록 B

핵심 가드: ScreenedStock/FactorScores 신규 필드 추가 시 매퍼 또는 의도적
제외 목록을 갱신하지 않으면 *_field_coverage 테스트가 깨진다.
"""
from __future__ import annotations

from datetime import date

from screening.schemas import (
    FactorScores,
    PeerComparable,
    ScreenedStock,
)

from agents.bull_bear import mappers
from agents.bull_bear.mappers import screened_to_context
from agents.bull_bear.schemas import (
    FundamentalsTimeseries,
    PriceSummary,
    QuarterlyFigures,
    StockContext,
)


# ---------- 헬퍼 ----------


def _factors() -> FactorScores:
    return FactorScores(
        momentum_12_1m=0.18,
        momentum_6m=0.09,
        pe_ttm=28.0,
        ev_ebitda=22.0,
        fcf_yield=0.04,
        momentum_z=1.2,
        value_z=0.3,
    )


def _screened(
    *,
    symbol: str = "AAPL",
    peer_context: list[PeerComparable] | None = None,
    data_quality_flags: list[str] | None = None,
) -> ScreenedStock:
    return ScreenedStock(
        symbol=symbol,
        company_name="Apple Inc.",
        sector="Technology",
        sub_sector="Consumer Electronics",
        rank=1,
        composite_score=1.5,
        factors=_factors(),
        peer_context=peer_context if peer_context is not None else [],
        data_quality_flags=data_quality_flags if data_quality_flags is not None else [],
    )


def _map(stock: ScreenedStock) -> StockContext:
    """공통 외부 주입 인자로 매퍼 호출."""
    return screened_to_context(
        stock,
        as_of_date=date(2026, 5, 4),
        run_id="2026-05-04T00:00:00Z",
        screening_s3_key="screening/dt=2026-05-04/result.json",
        price_summary=PriceSummary(return_1y=0.18, beta_1y=1.1),
        fundamentals=FundamentalsTimeseries(
            quarters=[QuarterlyFigures(period_end=date(2026, 3, 31), revenue=95_000_000_000.0)],
            revenue_cagr_5y=0.08,
        ),
    )


# ---------- 매핑 가드 (부록 B) ----------


def test_screened_stock_field_coverage():
    """ScreenedStock 신규 필드 추가 시 가드가 깨져 매퍼 갱신을 강제한다."""
    expected = set(ScreenedStock.model_fields.keys())
    declared = (
        mappers._SCREENED_STOCK_HANDLED
        | mappers._SCREENED_STOCK_INTENTIONALLY_DROPPED
    )
    missing = expected - declared
    extraneous = declared - expected
    assert not missing, f"매퍼가 다루지 않는 ScreenedStock 필드: {missing}"
    assert not extraneous, f"존재하지 않는 ScreenedStock 필드 선언: {extraneous}"


def test_factor_scores_field_coverage():
    """FactorScores 신규 필드 추가 시 가드가 깨져 평탄화 갱신을 강제한다."""
    expected = set(FactorScores.model_fields.keys())
    declared = (
        mappers._FACTOR_SCORES_FLATTENED
        | mappers._FACTOR_SCORES_INTENTIONALLY_DROPPED
    )
    missing = expected - declared
    extraneous = declared - expected
    assert not missing, f"매퍼가 다루지 않는 FactorScores 필드: {missing}"
    assert not extraneous, f"존재하지 않는 FactorScores 필드 선언: {extraneous}"


def test_handled_and_dropped_are_disjoint():
    """한 필드가 'handled' 와 'dropped' 양쪽에 들어 있으면 의도가 모호."""
    assert not (
        mappers._SCREENED_STOCK_HANDLED & mappers._SCREENED_STOCK_INTENTIONALLY_DROPPED
    )
    assert not (
        mappers._FACTOR_SCORES_FLATTENED & mappers._FACTOR_SCORES_INTENTIONALLY_DROPPED
    )


# ---------- 1:1 평탄화 정확성 ----------


def test_identity_fields_pass_through():
    ctx = _map(_screened())
    assert ctx.symbol == "AAPL"
    assert ctx.company_name == "Apple Inc."
    assert ctx.sector == "Technology"
    assert ctx.sub_sector == "Consumer Electronics"


def test_composite_score_pass_through():
    ctx = _map(_screened())
    assert ctx.composite_score == 1.5


def test_factors_flattened_to_top_level():
    ctx = _map(_screened())
    f = _factors()
    assert ctx.momentum_z == f.momentum_z
    assert ctx.value_z == f.value_z
    assert ctx.pe_ttm == f.pe_ttm
    assert ctx.ev_ebitda == f.ev_ebitda
    assert ctx.fcf_yield == f.fcf_yield


def test_raw_momentum_components_not_exposed_in_context():
    """LLM 미노출 정책 — StockContext 자체에 raw 모멘텀 필드가 없어야 함."""
    fields = set(StockContext.model_fields.keys())
    assert "momentum_12_1m" not in fields
    assert "momentum_6m" not in fields


def test_rank_not_exposed_in_context():
    """rank 는 의도적 제외 — 동어반복 논거 위험."""
    assert "rank" not in StockContext.model_fields


def test_peer_context_passes_through_with_same_type():
    peers = [
        PeerComparable(symbol="MSFT", pe_ttm=30.0, ev_ebitda=21.0),
        PeerComparable(symbol="GOOGL", pe_ttm=25.0, fcf_yield=0.05),
    ]
    ctx = _map(_screened(peer_context=peers))
    assert len(ctx.peer_context) == 2
    assert ctx.peer_context[0] == peers[0]
    assert ctx.peer_context[1] == peers[1]


def test_data_quality_flags_pass_through():
    ctx = _map(_screened(data_quality_flags=["missing_fcf", "negative_earnings"]))
    assert ctx.data_quality_flags == ["missing_fcf", "negative_earnings"]


# ---------- 외부 주입 필드 ----------


def test_injected_fields_set_on_context():
    ctx = _map(_screened())
    assert ctx.as_of_date == date(2026, 5, 4)
    assert ctx.run_id == "2026-05-04T00:00:00Z"
    assert ctx.screening_s3_key == "screening/dt=2026-05-04/result.json"
    assert ctx.price_summary.return_1y == 0.18
    assert ctx.price_summary.beta_1y == 1.1
    assert len(ctx.fundamentals.quarters) == 1
    assert ctx.fundamentals.revenue_cagr_5y == 0.08


# ---------- 방어적 복사 ----------


def test_data_quality_flags_defensive_copy():
    """매퍼가 반환한 ctx 의 list 를 변경해도 입력 ScreenedStock 은 안 바뀜."""
    flags = ["missing_fcf"]
    stock = _screened(data_quality_flags=flags)
    ctx = _map(stock)
    ctx.data_quality_flags.append("downstream_added")
    assert stock.data_quality_flags == ["missing_fcf"]


def test_peer_context_list_defensive_copy():
    """peer_context list 자체도 방어적 복사 — 항목(PeerComparable) 은 immutable
    한 의도이므로 원소까지 deep copy 할 필요는 없음."""
    peers = [PeerComparable(symbol="MSFT")]
    stock = _screened(peer_context=peers)
    ctx = _map(stock)
    ctx.peer_context.append(PeerComparable(symbol="EXTRA"))
    assert len(stock.peer_context) == 1


# ---------- None / 결측 처리 ----------


def test_optional_factor_components_pass_none():
    """factors 내 결측 컴포넌트가 그대로 None 으로 옮겨지는지."""
    stock = ScreenedStock(
        symbol="X",
        rank=1,
        composite_score=0.5,
        factors=FactorScores(),  # 모두 None
    )
    ctx = _map(stock)
    assert ctx.momentum_z is None
    assert ctx.value_z is None
    assert ctx.pe_ttm is None
    assert ctx.ev_ebitda is None
    assert ctx.fcf_yield is None


def test_optional_identity_fields_pass_none():
    """company_name/sector/sub_sector 가 None 인 ScreenedStock 도 매핑 가능."""
    stock = ScreenedStock(
        symbol="X",
        company_name=None,
        sector=None,
        sub_sector=None,
        rank=1,
        composite_score=0.5,
        factors=FactorScores(),
    )
    ctx = _map(stock)
    assert ctx.company_name is None
    assert ctx.sector is None
    assert ctx.sub_sector is None
