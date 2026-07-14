"""시나리오 lambda_core.handle 통합 테스트.

설계 근거: docs/03-scenario.md §6.1, §6.2, §9

I/O 모킹 (M2 패턴): s3_io 함수(read_json/read_parquet/write_text/get_secret) +
분기 income cache-aside 를 monkeypatch (인메모리 store). caller 는 FakeAnthropic,
fmp 는 sentinel. 실제 네트워크/S3/LLM 호출 없음.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace

import pyarrow as pa
import pytest

from common.ohlcv import OHLCV_SCHEMA
from screening.schemas import ScreenedStock

from agents.bull_bear.agent import RawCompletion
from agents.bull_bear.schemas import Argument, BullBearOpinion
from agents.scenario import lambda_core
from agents.scenario.schemas import ExpectedReturnsBundle, ScenarioOpinion

AS_OF = "2026-05-04"
DT = "dt=2026-05-04"
BULL_KEY = f"agents/bullbear/{DT}/symbol=AAPL/stance=bull.json"
BEAR_KEY = f"agents/bullbear/{DT}/symbol=AAPL/stance=bear.json"
OHLCV_KEY = "ohlcv/ticker=AAPL/data.parquet"
SCENARIOS_KEY = f"scenarios/{DT}/symbol=AAPL.json"
ER_KEY = f"expected_returns/{DT}/symbol=AAPL.json"
CTX_KEY = f"scenario_contexts/{DT}/symbol=AAPL.json"


# ---------- Fake ----------


@dataclass
class _FakeAnthropic:
    responses: list[RawCompletion]
    calls: int = 0

    def call(self, *, model, system, user, max_tokens, temperature) -> RawCompletion:
        self.calls += 1
        if not self.responses:
            raise RuntimeError("응답 큐 소진")
        return self.responses.pop(0)


def _bb_payload(stance: str, symbol: str = "AAPL") -> dict[str, object]:
    return BullBearOpinion(
        symbol=symbol, stance=stance, as_of_date=date(2026, 5, 4), summary=f"{stance} s",  # type: ignore[arg-type]
        arguments=[Argument(claim="c", evidence="e", confidence="high")] * 3,
        key_risks_to_thesis=["r"], model="m", input_tokens=0, output_tokens=0, cost_usd=0.0,
    ).model_dump(mode="json")


def _scenarios_payload() -> dict[str, object]:
    def _trig(metric: str = "revenue_yoy") -> dict[str, object]:
        return {"metric": metric, "direction": "less_than", "threshold": 5.0,
                "threshold_unit": "percent", "description": "metric below threshold pct"}
    return {
        "scenarios": [
            {"label": "bull", "probability": 0.4, "narrative": "bull case cites Bull margin evidence", "invalidation_trigger": _trig()},
            {"label": "base", "probability": 0.45, "narrative": "base case cites steady fundamentals", "invalidation_trigger": _trig("eps_yoy")},
            {"label": "bear", "probability": 0.15, "narrative": "bear case cites Bear demand risk", "invalidation_trigger": _trig("fcf_yoy")},
        ]
    }


def _completion() -> RawCompletion:
    return RawCompletion(text=json.dumps(_scenarios_payload()), input_tokens=3300, output_tokens=500)


def _ohlcv(closes: list[float]) -> pa.Table:
    n = len(closes)
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(n)]
    return pa.table(
        {"date": dates, "open": closes, "high": closes, "low": closes,
         "close": closes, "adj_close": closes, "volume": [1_000_000] * n},
        schema=OHLCV_SCHEMA,
    )


def _income() -> list[dict[str, object]]:
    dates = ["2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"]
    return [{"date": d, "revenue": 1000.0, "epsDiluted": 1.5} for d in dates]


def _event(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "screened_stock": {
            "symbol": "AAPL", "company_name": "Apple Inc.",
            "sector": "Technology", "sub_sector": "Consumer Electronics",
            "rank": 1, "composite_score": 1.42,
            "factors": {"momentum_z": 0.95, "value_z": -0.3, "pe_ttm": 29.5},
            "peer_context": [{"symbol": "MSFT", "pe_ttm": 30.0}, {"symbol": "SONY", "pe_ttm": 20.0}],
            "data_quality_flags": [],
        },
        "as_of_date": AS_OF,
        "run_id": "test-run-001",
    }
    base.update(overrides)
    return base


# ---------- 픽스처 ----------


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("FMP_SECRET_ID", "fmp-secret")
    monkeypatch.setenv("ANTHROPIC_SECRET_ID", "anthropic-secret")


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch):
    json_store: dict[str, object] = {}
    parquet_store: dict[str, pa.Table] = {}
    writes: dict[str, str] = {}

    monkeypatch.setattr(lambda_core, "read_json", lambda bucket, key: json_store.get(key))
    monkeypatch.setattr(lambda_core, "read_parquet", lambda bucket, key: parquet_store.get(key))
    monkeypatch.setattr(lambda_core, "write_text", lambda bucket, key, body, **k: writes.__setitem__(key, body))
    monkeypatch.setattr(lambda_core, "get_secret", lambda sid: f"fake-{sid}")
    monkeypatch.setattr(lambda_core, "fetch_income_quarterly_with_cache", lambda *a, **k: _income())
    return SimpleNamespace(json=json_store, parquet=parquet_store, writes=writes)


def _seed_inputs(store) -> None:
    store.json[BULL_KEY] = {"opinion": _bb_payload("bull")}
    store.json[BEAR_KEY] = {"opinion": _bb_payload("bear")}
    store.parquet[OHLCV_KEY] = _ohlcv([100.0, 150.0, 120.0])


# ---------- 환경/입력 ----------


def test_env_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S3_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="S3_BUCKET"):
        lambda_core.handle(_event(), None, caller=_FakeAnthropic([]), fmp=object())


# ---------- skip 경로 (§9) ----------


def test_skip_when_bullbear_missing(env: None, store) -> None:
    # 의견 미시드 → read_json None → skip
    store.parquet[OHLCV_KEY] = _ohlcv([100.0])
    out = lambda_core.handle(_event(), None, caller=_FakeAnthropic([_completion()]), fmp=object())
    assert out["status"] == "skipped"
    assert out["reason"] == "bullbear_missing"
    assert SCENARIOS_KEY not in store.writes  # LLM/저장 안 함


def test_skip_when_only_bull_present(env: None, store) -> None:
    store.json[BULL_KEY] = {"opinion": _bb_payload("bull")}
    store.parquet[OHLCV_KEY] = _ohlcv([100.0])
    out = lambda_core.handle(_event(), None, caller=_FakeAnthropic([_completion()]), fmp=object())
    assert out["status"] == "skipped"
    assert out["bull_loaded"] is True and out["bear_loaded"] is False


def test_skip_on_context_error_empty_ohlcv(env: None, store) -> None:
    store.json[BULL_KEY] = {"opinion": _bb_payload("bull")}
    store.json[BEAR_KEY] = {"opinion": _bb_payload("bear")}
    # OHLCV 미시드 → current_price 산정 불가 → ScenarioContextError → skip
    out = lambda_core.handle(_event(), None, caller=_FakeAnthropic([_completion()]), fmp=object())
    assert out["status"] == "skipped"
    assert out["reason"] == "context_error"


# ---------- cache miss ----------


def test_cache_miss_calls_llm_and_writes(env: None, store) -> None:
    _seed_inputs(store)
    fake = _FakeAnthropic([_completion()])
    out = lambda_core.handle(_event(), None, caller=fake, fmp=object())

    assert out["status"] == "ok"
    assert out["cache"] == "miss"
    assert out["attempts"] == 1
    assert fake.calls == 1
    assert out["cost_usd"] == pytest.approx(0.0174)
    # S3 저장 3개
    assert SCENARIOS_KEY in store.writes
    assert ER_KEY in store.writes
    assert CTX_KEY in store.writes
    # 저장본 검증
    saved_opinion = json.loads(store.writes[SCENARIOS_KEY])
    assert "scenario_opinion" in saved_opinion
    assert saved_opinion["input_hash"] == out["input_hash"]
    bundle = ExpectedReturnsBundle.model_validate(json.loads(store.writes[ER_KEY]))
    # #12 sensitivity — primary + 대안 3종 (balanced/base_cap_10/aggressive)
    assert set(bundle.alternatives) == {"balanced", "base_cap_10", "aggressive"}
    assert set(out["alternatives_expected_return"]) == {"balanced", "base_cap_10", "aggressive"}
    ScenarioOpinion.model_validate(saved_opinion["scenario_opinion"])


def test_miss_data_quality_flags_propagate(env: None, store) -> None:
    # 이 fixture 는 base(현재가 cap=120) < bear peer target(135) 로 price_order
    # 위반 (v0.3) → flag 가 출력·ExpectedReturn 저장본에 전파되는지 검증
    _seed_inputs(store)
    out = lambda_core.handle(_event(), None, caller=_FakeAnthropic([_completion()]), fmp=object())
    assert "expected_return" in out
    assert any("price_order_violation" in f for f in out["data_quality_flags"])
    bundle = ExpectedReturnsBundle.model_validate(json.loads(store.writes[ER_KEY]))
    assert bundle.primary.data_quality_flags == out["data_quality_flags"]


# ---------- cache hit ----------


def test_cache_hit_skips_llm_but_recomputes_pricing(env: None, store) -> None:
    _seed_inputs(store)
    # 1차 miss → scenarios 저장
    first = lambda_core.handle(_event(), None, caller=_FakeAnthropic([_completion()]), fmp=object())
    # 저장본을 read 경로로 이동 (운영 재실행 시뮬레이션)
    store.json[SCENARIOS_KEY] = json.loads(store.writes[SCENARIOS_KEY])
    store.writes.clear()

    # 2차 — caller 응답 큐 비어 있어도 hit 이면 호출 안 함
    fake = _FakeAnthropic([])
    second = lambda_core.handle(_event(), None, caller=fake, fmp=object())

    assert second["cache"] == "hit"
    assert second["cost_usd"] == 0.0
    assert second["attempts"] == 0
    assert fake.calls == 0  # LLM 호출 생략
    assert second["input_hash"] == first["input_hash"]
    # 가격은 캐시 hit 에도 재실행 → expected_return 재저장 (§6.2)
    assert ER_KEY in store.writes
    assert CTX_KEY in store.writes
    assert SCENARIOS_KEY not in store.writes  # opinion 은 재저장 안 함


# ---------- pricing config override (§4.3) ----------


def test_pricing_config_override_applied(env: None, store) -> None:
    _seed_inputs(store)
    out = lambda_core.handle(
        _event(pricing_config_override={"base_price_cap_pct": None}),
        None, caller=_FakeAnthropic([_completion()]), fmp=object(),
    )
    bundle = ExpectedReturnsBundle.model_validate(json.loads(store.writes[ER_KEY]))
    assert bundle.primary.pricing_config.base_price_cap_pct is None
    # 대안은 base override 와 무관하게 자기 config 유지 (base_cap_10 = 0.10)
    assert bundle.alternatives["base_cap_10"].pricing_config.base_price_cap_pct == 0.10


# ---------- agent 실패 ----------


def test_agent_failure_raises(env: None, store) -> None:
    from agents.scenario.agent import ScenarioAgentError

    _seed_inputs(store)
    bad = RawCompletion(text="not json", input_tokens=10, output_tokens=10)
    fake = _FakeAnthropic([bad, bad, bad])
    with pytest.raises(ScenarioAgentError):
        lambda_core.handle(_event(), None, caller=fake, fmp=object())
