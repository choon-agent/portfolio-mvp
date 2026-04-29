"""Bull/Bear 에이전트 스키마 단위 테스트.

순수 Pydantic 검증만 — 네트워크, S3, AWS, LLM 호출 없음.
설계 근거: docs/02-bull-bear.md §2.1, §2.2
"""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from agents.bull_bear.schemas import (
    Argument,
    BullBearOpinion,
    FundamentalsTimeseries,
    PeerComparable,
    PriceSummary,
    QuarterlyFigures,
    StockContext,
)


# ---------- 헬퍼 ----------


def _stock_context(**overrides: object) -> StockContext:
    base: dict[str, object] = {
        "symbol": "AAPL",
        "company_name": "Apple Inc.",
        "sector": "Technology",
        "sub_sector": "Consumer Electronics",
        "as_of_date": date(2026, 5, 4),
        "composite_score": 1.5,
        "momentum_z": 1.2,
        "value_z": 0.3,
        "pe_ttm": 28.0,
        "ev_ebitda": 22.0,
        "fcf_yield": 0.04,
        "peer_context": [],
        "price_summary": PriceSummary(return_1y=0.18),
        "fundamentals": FundamentalsTimeseries(),
        "run_id": "2026-05-04T00:00:00Z",
        "screening_s3_key": "screening/dt=2026-05-04/result.json",
    }
    base.update(overrides)
    return StockContext(**base)  # type: ignore[arg-type]


def _argument(claim: str = "Earnings momentum strong") -> Argument:
    return Argument(claim=claim, evidence="Revenue +12% YoY", confidence="medium")


def _opinion(**overrides: object) -> BullBearOpinion:
    base: dict[str, object] = {
        "symbol": "AAPL",
        "stance": "bull",
        "as_of_date": date(2026, 5, 4),
        "summary": "Strong fundamentals with momentum tailwind.",
        "arguments": [_argument(f"claim {i}") for i in range(3)],
        "key_risks_to_thesis": ["Valuation rich vs peers"],
        "model": "claude-sonnet-4-6",
        "input_tokens": 3200,
        "output_tokens": 580,
        "cost_usd": 0.0183,
    }
    base.update(overrides)
    return BullBearOpinion(**base)  # type: ignore[arg-type]


# ---------- QuarterlyFigures ----------


def test_quarterly_figures_period_end_required():
    with pytest.raises(ValidationError):
        QuarterlyFigures()  # type: ignore[call-arg]


def test_quarterly_figures_metrics_optional():
    q = QuarterlyFigures(period_end=date(2026, 3, 31))
    assert q.revenue is None
    assert q.eps_diluted is None
    assert q.fcf is None


# ---------- PriceSummary ----------


def test_price_summary_all_fields_optional():
    ps = PriceSummary()
    assert ps.return_1y is None
    assert ps.beta_1y is None


def test_price_summary_accepts_partial_population():
    ps = PriceSummary(return_1y=0.15, beta_1y=1.1)
    assert ps.return_1y == 0.15
    assert ps.beta_1y == 1.1
    assert ps.pct_from_52w_high is None


# ---------- FundamentalsTimeseries ----------


def test_fundamentals_timeseries_defaults_to_empty_quarters():
    fts = FundamentalsTimeseries()
    assert fts.quarters == []
    assert fts.revenue_cagr_5y is None


def test_fundamentals_timeseries_caps_quarters_at_4():
    quarters = [QuarterlyFigures(period_end=date(2026, 3, 31)) for _ in range(5)]
    with pytest.raises(ValidationError):
        FundamentalsTimeseries(quarters=quarters)


def test_fundamentals_timeseries_accepts_up_to_4_quarters():
    quarters = [
        QuarterlyFigures(period_end=date(2026 - (i // 4), 3, 31)) for i in range(4)
    ]
    fts = FundamentalsTimeseries(quarters=quarters)
    assert len(fts.quarters) == 4


# ---------- StockContext ----------


def test_stock_context_required_fields():
    """symbol/as_of_date/composite_score/run_id/s3_key/price_summary/fundamentals 누락 거부."""
    with pytest.raises(ValidationError):
        StockContext()  # type: ignore[call-arg]


def test_stock_context_defaults_for_optional_collections():
    ctx = _stock_context()
    assert ctx.peer_context == []
    assert ctx.data_quality_flags == []


def test_stock_context_peer_context_capped_at_5():
    """ScreenedStock.peer_context max_length=5 와 정합 (1:1 평탄화 가드)."""
    peers = [PeerComparable(symbol=f"P{i}") for i in range(6)]
    with pytest.raises(ValidationError):
        _stock_context(peer_context=peers)


def test_stock_context_accepts_screening_peer_comparable():
    """screening.schemas.PeerComparable 을 그대로 받는다 (재export 검증)."""
    peers = [PeerComparable(symbol="MSFT", pe_ttm=30.0, ev_ebitda=21.0)]
    ctx = _stock_context(peer_context=peers)
    assert ctx.peer_context[0].symbol == "MSFT"
    assert ctx.peer_context[0].pe_ttm == 30.0


def test_stock_context_negative_fcf_yield_preserved():
    """음수 FCF yield 는 정당한 부정 시그널 — 거부하지 않음 (docs §3.2 factors)."""
    ctx = _stock_context(fcf_yield=-0.08)
    assert ctx.fcf_yield == -0.08


def test_stock_context_lineage_fields_required():
    """run_id 와 screening_s3_key 는 audit 의무 필드 — 누락 거부."""
    with pytest.raises(ValidationError):
        _stock_context(run_id=None)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _stock_context(screening_s3_key=None)  # type: ignore[arg-type]


def test_stock_context_json_roundtrip_preserves_all_fields():
    """S3 저장본이 LLM 미노출 필드까지 보존하는지 (lineage 무결성)."""
    original = _stock_context(
        peer_context=[PeerComparable(symbol="MSFT", pe_ttm=30.0)],
        data_quality_flags=["missing_fcf"],
    )
    restored = StockContext.model_validate_json(original.model_dump_json())
    assert restored == original
    assert restored.data_quality_flags == ["missing_fcf"]
    assert restored.run_id == original.run_id


# ---------- Argument ----------


def test_argument_requires_non_empty_claim_and_evidence():
    with pytest.raises(ValidationError):
        Argument(claim="", evidence="x", confidence="low")
    with pytest.raises(ValidationError):
        Argument(claim="x", evidence="", confidence="low")


def test_argument_confidence_literal_only():
    with pytest.raises(ValidationError):
        Argument(claim="x", evidence="y", confidence="extreme")  # type: ignore[arg-type]


def test_argument_accepts_three_confidence_levels():
    for level in ("low", "medium", "high"):
        a = Argument(claim="x", evidence="y", confidence=level)  # type: ignore[arg-type]
        assert a.confidence == level


# ---------- BullBearOpinion ----------


def test_opinion_stance_literal_only():
    with pytest.raises(ValidationError):
        _opinion(stance="neutral")  # type: ignore[arg-type]


def test_opinion_arguments_min_3():
    with pytest.raises(ValidationError):
        _opinion(arguments=[_argument("a"), _argument("b")])


def test_opinion_arguments_max_5():
    with pytest.raises(ValidationError):
        _opinion(arguments=[_argument(f"c{i}") for i in range(6)])


def test_opinion_arguments_3_to_5_accepted():
    for n in (3, 4, 5):
        op = _opinion(arguments=[_argument(f"c{i}") for i in range(n)])
        assert len(op.arguments) == n


def test_opinion_summary_max_200_chars():
    with pytest.raises(ValidationError):
        _opinion(summary="x" * 201)


def test_opinion_summary_must_be_non_empty():
    with pytest.raises(ValidationError):
        _opinion(summary="")


def test_opinion_key_risks_min_1():
    with pytest.raises(ValidationError):
        _opinion(key_risks_to_thesis=[])


def test_opinion_key_risks_max_3():
    with pytest.raises(ValidationError):
        _opinion(key_risks_to_thesis=["r1", "r2", "r3", "r4"])


def test_opinion_rejects_negative_token_counts():
    with pytest.raises(ValidationError):
        _opinion(input_tokens=-1)
    with pytest.raises(ValidationError):
        _opinion(output_tokens=-1)


def test_opinion_rejects_negative_cost():
    with pytest.raises(ValidationError):
        _opinion(cost_usd=-0.001)


def test_opinion_zero_tokens_and_cost_allowed():
    """재시도/폴백 합산 시 개별 호출이 0 으로 기록되는 케이스 허용."""
    op = _opinion(input_tokens=0, output_tokens=0, cost_usd=0.0)
    assert op.cost_usd == 0.0


def test_opinion_model_must_be_non_empty():
    with pytest.raises(ValidationError):
        _opinion(model="")


def test_opinion_json_roundtrip():
    """LLM 응답 JSON 파싱 → 재직렬화 → 동일 객체 (검증 안정성)."""
    original = _opinion()
    restored = BullBearOpinion.model_validate_json(original.model_dump_json())
    assert restored == original
