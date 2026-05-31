"""시나리오 context_builder 단위 테스트.

순수 함수 — 네트워크/S3/FMP 호출 없음. OHLCV pa.Table 픽스처 + FMP dict 픽스처.
설계 근거: docs/03-scenario.md §2.1, §3.3, §9
"""
from __future__ import annotations

from datetime import date, timedelta

import pyarrow as pa
import pytest

from common.ohlcv import OHLCV_SCHEMA
from screening.schemas import FactorScores, PeerComparable, ScreenedStock

from agents.bull_bear.schemas import Argument, BullBearOpinion
from agents.scenario.context_builder import (
    ScenarioContextError,
    _peer_pe,
    _price_context,
    _ttm_eps,
    build_context,
    to_prompt_markdown,
)

AS_OF = date(2026, 5, 4)


# ---------- 픽스처 ----------


def _ohlcv(closes: list[float], start: date = date(2024, 1, 2)) -> pa.Table:
    n = len(closes)
    dates = [start + timedelta(days=i) for i in range(n)]
    return pa.table(
        {
            "date": dates,
            "open": list(closes),
            "high": list(closes),
            "low": list(closes),
            "close": list(closes),
            "adj_close": list(closes),
            "volume": [1_000_000] * n,
        },
        schema=OHLCV_SCHEMA,
    )


def _empty_ohlcv() -> pa.Table:
    return pa.table({name: [] for name in OHLCV_SCHEMA.names}, schema=OHLCV_SCHEMA)


def _screened(**overrides: object) -> ScreenedStock:
    base: dict[str, object] = {
        "symbol": "AAPL",
        "company_name": "Apple Inc.",
        "sector": "Technology",
        "sub_sector": "Consumer Electronics",
        "rank": 1,
        "composite_score": 1.5,
        "factors": FactorScores(momentum_z=1.2, value_z=0.3, pe_ttm=28.0),
        "peer_context": [
            PeerComparable(symbol="MSFT", pe_ttm=30.0),
            PeerComparable(symbol="DELL", pe_ttm=None),  # 결측 → 제외
            PeerComparable(symbol="HPQ", pe_ttm=-5.0),   # 음수 → 제외
            PeerComparable(symbol="SONY", pe_ttm=20.0),
        ],
    }
    base.update(overrides)
    return ScreenedStock(**base)  # type: ignore[arg-type]


def _opinion(stance: str, symbol: str = "AAPL") -> BullBearOpinion:
    return BullBearOpinion(
        symbol=symbol,
        stance=stance,  # type: ignore[arg-type]
        as_of_date=AS_OF,
        summary=f"{stance} summary text",
        arguments=[
            Argument(claim=f"{stance} claim 1", evidence="rev +12% YoY", confidence="high"),
            Argument(claim=f"{stance} claim 2", evidence="margin expansion", confidence="medium"),
            Argument(claim=f"{stance} claim 3", evidence="buyback", confidence="low"),
        ],
        key_risks_to_thesis=[f"{stance} risk"],
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.018,
    )


def _income(epss: list[float | None], start_year: int = 2025) -> list[dict[str, object]]:
    # 분기 income 응답 (date desc 무관 — 코드가 정렬). 최신이 앞.
    rows: list[dict[str, object]] = []
    for i, eps in enumerate(epss):
        month = 12 - 3 * i
        year = start_year
        while month <= 0:
            month += 12
            year -= 1
        rows.append({"date": f"{year}-{month:02d}-28", "revenue": 1000.0, "epsdiluted": eps})
    return rows


# ---------- _price_context ----------


def test_price_context_basic() -> None:
    # closes 1~100, current=100, 52w(=직전 252, 여기선 전체) high=100 low=1
    closes = [float(i) for i in range(1, 101)]
    current, r_high, r_low = _price_context(_ohlcv(closes), AS_OF)
    assert current == 100.0
    assert r_high == pytest.approx(0.0)            # high==current
    assert r_low == pytest.approx((1 - 100) / 100)  # -0.99


def test_price_context_upside_to_high() -> None:
    # 최근 close 가 과거 high 보다 낮음 → return_52w_high > 0
    closes = [100.0, 150.0, 120.0]
    current, r_high, r_low = _price_context(_ohlcv(closes), AS_OF)
    assert current == 120.0
    assert r_high == pytest.approx((150 - 120) / 120)  # +0.25
    assert r_low == pytest.approx((100 - 120) / 120)   # -0.1667


def test_price_context_empty_ohlcv() -> None:
    assert _price_context(_empty_ohlcv(), AS_OF) == (None, None, None)
    assert _price_context(None, AS_OF) == (None, None, None)


def test_price_context_lookahead_blocked() -> None:
    # as_of 이후 데이터 무시
    closes = [10.0, 20.0, 999.0]
    as_of = date(2024, 1, 3)  # 3번째(999) 제외
    current, _, _ = _price_context(_ohlcv(closes), as_of)
    assert current == 20.0


# ---------- _ttm_eps ----------


def test_ttm_eps_sum_last_4() -> None:
    assert _ttm_eps(_income([2.0, 1.5, 1.0, 0.5, 3.0])) == pytest.approx(5.0)  # 2+1.5+1+0.5


def test_ttm_eps_insufficient_quarters() -> None:
    assert _ttm_eps(_income([2.0, 1.5, 1.0])) is None  # < 4


def test_ttm_eps_missing_value_none() -> None:
    assert _ttm_eps(_income([2.0, None, 1.0, 0.5])) is None


def test_ttm_eps_empty() -> None:
    assert _ttm_eps([]) is None


# ---------- _peer_pe ----------


def test_peer_pe_filters_invalid() -> None:
    # MSFT 30, SONY 20 만 (DELL None, HPQ -5 제외)
    assert sorted(_peer_pe(_screened())) == [20.0, 30.0]


def test_peer_pe_empty() -> None:
    assert _peer_pe(_screened(peer_context=[])) == []


# ---------- build_context ----------


def test_build_context_happy() -> None:
    ctx = build_context(
        _screened(),
        _opinion("bull"),
        _opinion("bear"),
        as_of_date=AS_OF,
        run_id="2026-05-04T00:00:00Z",
        scenario_s3_key="scenarios/dt=2026-05-04/symbol=AAPL.json",
        bullbear_s3_keys={"bull": "kb", "bear": "kr"},
        ohlcv=_ohlcv([100.0, 150.0, 120.0]),
        income_quarterly=_income([2.0, 1.5, 1.0, 0.5]),
    )
    assert ctx.symbol == "AAPL"
    assert ctx.sector == "Technology"
    assert ctx.current_price == 120.0
    assert ctx.ttm_eps == pytest.approx(5.0)
    assert sorted(ctx.peer_pe) == [20.0, 30.0]
    assert ctx.bull_opinion.stance == "bull"
    assert ctx.data_quality_flags == []


def test_build_context_empty_ohlcv_raises() -> None:
    with pytest.raises(ScenarioContextError, match="current_price 산정 불가"):
        build_context(
            _screened(), _opinion("bull"), _opinion("bear"),
            as_of_date=AS_OF, run_id="r", scenario_s3_key="k",
            bullbear_s3_keys={"bull": "kb", "bear": "kr"},
            ohlcv=_empty_ohlcv(),
        )


def test_build_context_negative_price_raises() -> None:
    with pytest.raises(ScenarioContextError, match="current_price"):
        build_context(
            _screened(), _opinion("bull"), _opinion("bear"),
            as_of_date=AS_OF, run_id="r", scenario_s3_key="k",
            bullbear_s3_keys={"bull": "kb", "bear": "kr"},
            ohlcv=_ohlcv([-1.0]),
        )


def test_build_context_stance_mismatch_raises() -> None:
    with pytest.raises(ScenarioContextError, match="stance 불일치"):
        build_context(
            _screened(), _opinion("bear"), _opinion("bull"),  # swapped
            as_of_date=AS_OF, run_id="r", scenario_s3_key="k",
            bullbear_s3_keys={"bull": "kb", "bear": "kr"},
            ohlcv=_ohlcv([100.0]),
        )


def test_build_context_symbol_mismatch_raises() -> None:
    with pytest.raises(ScenarioContextError, match="symbol 불일치"):
        build_context(
            _screened(), _opinion("bull", symbol="XOM"), _opinion("bear"),
            as_of_date=AS_OF, run_id="r", scenario_s3_key="k",
            bullbear_s3_keys={"bull": "kb", "bear": "kr"},
            ohlcv=_ohlcv([100.0]),
        )


# ---------- to_prompt_markdown ----------


def _ctx_for_prompt() -> object:
    return build_context(
        _screened(),
        _opinion("bull"),
        _opinion("bear"),
        as_of_date=AS_OF,
        run_id="SECRET_RUN_ID",
        scenario_s3_key="SECRET_SCENARIO_KEY",
        bullbear_s3_keys={"bull": "SECRET_BULL_KEY", "bear": "SECRET_BEAR_KEY"},
        ohlcv=_ohlcv([100.0, 150.0, 120.0]),
        income_quarterly=_income([2.0, 1.5, 1.0, 0.5]),
    )


def test_prompt_includes_identity_price_opinions() -> None:
    md = to_prompt_markdown(_ctx_for_prompt())  # type: ignore[arg-type]
    assert "# AAPL — Apple Inc." in md
    assert "## Price Context" in md
    assert "Current price: $120.00" in md
    assert "## Bull Opinion" in md
    assert "## Bear Opinion" in md
    assert "bull summary text" in md
    assert "[high] bull claim 1 — rev +12% YoY" in md


def test_prompt_excludes_lineage() -> None:
    # §10 리뷰 A 갭 — lineage·opinion 메타가 프롬프트에 새지 않아야 함
    md = to_prompt_markdown(_ctx_for_prompt())  # type: ignore[arg-type]
    for secret in (
        "SECRET_RUN_ID",
        "SECRET_SCENARIO_KEY",
        "SECRET_BULL_KEY",
        "SECRET_BEAR_KEY",
        "claude-sonnet-4-6",  # opinion model 메타
        "cost_usd",
        "input_tokens",
    ):
        assert secret not in md


def test_prompt_deterministic() -> None:
    assert to_prompt_markdown(_ctx_for_prompt()) == to_prompt_markdown(_ctx_for_prompt())  # type: ignore[arg-type]
