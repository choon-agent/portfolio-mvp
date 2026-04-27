"""스크리닝 파이프라인 통합 테스트.

순수 함수만 — 네트워크/S3/AWS 호출 없음. 실제 데이터 형태의 픽스처로 5개 모듈
합성 동작 검증.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pyarrow as pa
import pytest

from common.models import Constituent
from common.ohlcv import OHLCV_SCHEMA
from screening.factors import (
    FMP_FIELD_EARNINGS_YIELD_TTM,
    FMP_FIELD_EV_EBITDA_TTM,
    FMP_FIELD_FCF_YIELD_TTM,
)
from screening.pipeline import _default_run_id, run_screening


AS_OF = date(2026, 5, 4)


# ---------- 픽스처 빌더 ----------


def _price_history(prices: list[float], end_date: date, volume: int = 300_000) -> pa.Table:
    """오래된 → 최근 순으로 정렬된 OHLCV 테이블."""
    n = len(prices)
    dates = [end_date - timedelta(days=i) for i in range(n - 1, -1, -1)]
    return pa.table(
        {
            "date": dates,
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "adj_close": prices,
            "volume": [volume] * n,
        },
        schema=OHLCV_SCHEMA,
    )


def _build_fixture(
    n_stocks: int = 20,
    sub_sector: str = "SW",
    sector: str = "Tech",
    as_of_date: date = AS_OF,
) -> dict[str, Any]:
    """검증 통과 가능한 n_stocks 종목 픽스처.

    각 종목의 팩터값을 i 별로 다르게 설정해 점수 분포가 의미 있게 형성되도록 함.
    - 종목 i 의 momentum_12_1m = i / 100 (0%~)
    - 종목 i 의 P/E = 15 + i (낮을수록 좋음)
    """
    constituents = [
        Constituent(
            symbol=f"S{i:02d}",
            company_name=f"Company {i}",
            sector=sector,
            sub_sector=sub_sector,
            date_added=date(2010, 1, 1),
        )
        for i in range(n_stocks)
    ]

    market_caps: dict[str, float | None] = {c.symbol: 5_000_000_000.0 for c in constituents}
    price_histories: dict[str, pa.Table | None] = {}
    key_metrics_ttm: dict[str, dict[str, Any] | None] = {}

    for i, c in enumerate(constituents):
        # 253일 기본 100, t-21 위치(인덱스 231)만 100+i 로 → 모멘텀 12_1m = i/100
        prices = [100.0] * 253
        prices[231] = 100.0 + i
        prices[126] = 100.0 + i * 0.5  # 6m 도 변동
        price_histories[c.symbol] = _price_history(prices, as_of_date)

        # P/E TTM = 15 + i 가 되도록 earningsYieldTTM = 1/(15+i)
        key_metrics_ttm[c.symbol] = {
            FMP_FIELD_EARNINGS_YIELD_TTM: 1.0 / (15.0 + i),
            FMP_FIELD_EV_EBITDA_TTM: 10.0 + i * 0.5,
            FMP_FIELD_FCF_YIELD_TTM: 0.08 - i * 0.003,
        }

    return {
        "constituents": constituents,
        "market_caps": market_caps,
        "price_histories": price_histories,
        "key_metrics_ttm": key_metrics_ttm,
        "as_of_date": as_of_date,
    }


# ---------- _default_run_id ----------


def test_default_run_id_uses_as_of_date():
    assert _default_run_id(date(2026, 5, 4)) == "2026-05-04T00:00:00Z"


# ---------- 엔드투엔드 ----------


def test_run_screening_returns_valid_result():
    fixture = _build_fixture(n_stocks=20)
    result = run_screening(**fixture)

    # ScreeningResult 검증 통과 (15~20, rank 1..N, score desc — schemas.py)
    assert 15 <= len(result.selected) <= 20
    assert result.universe_size == 20
    assert result.as_of_date == AS_OF
    assert result.factor_weights == {"momentum": 0.5, "value": 0.5}
    # rank 1..N consecutive (schemas validator 가 강제)
    assert [s.rank for s in result.selected] == list(range(1, len(result.selected) + 1))


def test_run_screening_default_run_id_is_iso():
    fixture = _build_fixture()
    result = run_screening(**fixture)
    assert result.run_id == "2026-05-04T00:00:00Z"


def test_run_screening_custom_run_id_preserved():
    fixture = _build_fixture()
    result = run_screening(**fixture, run_id="manual-run-001")
    assert result.run_id == "manual-run-001"


def test_run_screening_propagates_custom_weights():
    fixture = _build_fixture()
    result = run_screening(**fixture, momentum_weight=0.7, value_weight=0.3)
    assert result.factor_weights == {"momentum": 0.7, "value": 0.3}


# ---------- 모듈 합성 검증 ----------


def test_run_screening_universe_filter_drops_propagate():
    fixture = _build_fixture(n_stocks=22)
    # 두 종목 시총을 임계값 이하로
    fixture["market_caps"]["S00"] = 100_000_000.0
    fixture["market_caps"]["S01"] = 100_000_000.0

    result = run_screening(**fixture)

    # universe_size = 통과 종목 수 (22 - 2)
    assert result.universe_size == 20
    selected_symbols = {s.symbol for s in result.selected}
    assert "S00" not in selected_symbols
    assert "S01" not in selected_symbols


def test_run_screening_attaches_peer_context_for_selected():
    """선정 종목 모두 같은 sub_sector — 19개 다른 종목 중 상위 5개가 peer."""
    fixture = _build_fixture(n_stocks=20)
    result = run_screening(**fixture)

    for s in result.selected:
        # 같은 sub_sector 에 19개 다른 종목 → 5 peers (자기 자신 제외)
        assert len(s.peer_context) == 5
        # peer 에 자기 자신 없음
        assert s.symbol not in {p.symbol for p in s.peer_context}


def test_run_screening_selected_carry_factors_with_z_scores():
    fixture = _build_fixture(n_stocks=20)
    result = run_screening(**fixture)

    for s in result.selected:
        # raw 팩터 + z-score 모두 채워짐 (factors → normalize 합성)
        assert s.factors.momentum_12_1m is not None
        assert s.factors.momentum_z is not None
        assert s.factors.pe_ttm is not None
        assert s.factors.value_z is not None


def test_run_screening_composite_score_uses_provided_weights():
    """composite = w_m × momentum_z + w_v × value_z 가 실제로 적용됨."""
    fixture = _build_fixture(n_stocks=20)
    result = run_screening(**fixture, momentum_weight=0.7, value_weight=0.3)

    top = result.selected[0]
    expected = 0.7 * top.factors.momentum_z + 0.3 * top.factors.value_z
    assert top.composite_score == pytest.approx(expected)


# ---------- 결정론·재현성 ----------


def test_run_screening_is_deterministic():
    """동일 입력 → 동일 출력 (재현성, schemas.py run_id 보존 포함)."""
    fixture = _build_fixture(n_stocks=20)
    result1 = run_screening(**fixture)
    result2 = run_screening(**fixture)
    assert result1 == result2


# ---------- 실패 경로 ----------


def test_run_screening_raises_when_too_few_stocks_pass():
    """universe 필터 통과 < target_min 이면 score.select_screened 가 ValueError."""
    fixture = _build_fixture(n_stocks=10)  # 모두 통과해도 10 < 15
    with pytest.raises(ValueError, match="target_min"):
        run_screening(**fixture)


def test_run_screening_raises_when_universe_filter_drops_too_many():
    fixture = _build_fixture(n_stocks=20)
    # 14 종목 시총을 임계값 이하로 → 6 만 통과 → < 15
    for i in range(14):
        fixture["market_caps"][f"S{i:02d}"] = 100_000_000.0
    with pytest.raises(ValueError, match="target_min"):
        run_screening(**fixture)
