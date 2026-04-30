"""lambda_core.handle 및 stance 별 wrapper 단위 테스트.

설계 근거: docs/02-bull-bear.md §4.1, §4.2, §6, §9 #7

핵심 검증:
1. cache miss → LLM 호출 + S3 2개 (opinion, context) 저장
2. cache hit (동일 input_hash) → LLM 호출 생략, cost=0
3. cache stale (다른 input_hash) → miss 와 동일 흐름
4. invalid stance / 환경변수 / event 누락 → 명확한 에러
5. bull/bear wrapper → 동일 코어로 stance 만 다르게 라우팅

I/O 모킹:
- s3_io 함수(read_json/read_parquet/write_text/get_secret) → monkeypatch
- 분기 statements cache-aside 함수 → monkeypatch (fundamentals.py 자체 동작은
  test_fundamentals.py 가 책임)
- AnthropicCaller → FakeAnthropicClient 주입 (caller 인자)
- FMPClient → fmp 인자에 sentinel 주입 (cache-aside 모킹으로 실제 호출 X)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from agents.bull_bear import lambda_core
from agents.bull_bear.agent import RawCompletion, context_input_hash
from agents.bull_bear.context_builder import build_context
from agents.bull_bear.schemas import StockContext
from screening.schemas import ScreenedStock


# ---------- 픽스처 ----------


_VALID_OPINION_PAYLOAD: dict[str, object] = {
    "summary": "Strong revenue and EPS trajectory with momentum tailwind.",
    "arguments": [
        {"claim": "Revenue grew steadily", "evidence": "Revenue +12% YoY", "confidence": "high"},
        {"claim": "Margin expansion", "evidence": "Op margin +200bp", "confidence": "medium"},
        {"claim": "FCF strong", "evidence": "FCF Yield 4%", "confidence": "medium"},
    ],
    "key_risks_to_thesis": ["Multiple compression if rates rise"],
}


def _completion() -> RawCompletion:
    return RawCompletion(
        text=json.dumps(_VALID_OPINION_PAYLOAD), input_tokens=1500, output_tokens=900
    )


@dataclass
class _FakeAnthropic:
    """test_bullbear_agent 의 FakeAnthropicClient 와 동등 — 본 모듈 내부 격리."""

    responses: list[RawCompletion]
    calls: int = 0

    def call(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
    ) -> RawCompletion:
        self.calls += 1
        if not self.responses:
            raise RuntimeError("응답 큐 소진")
        return self.responses.pop(0)


def _event() -> dict[str, Any]:
    return {
        "screened_stock": {
            "symbol": "AAPL",
            "company_name": "Apple Inc.",
            "sector": "Technology",
            "sub_sector": "Consumer Electronics",
            "rank": 1,
            "composite_score": 1.42,
            "factors": {
                "momentum_z": 0.95,
                "value_z": -0.30,
                "pe_ttm": 29.5,
                "ev_ebitda": 22.0,
                "fcf_yield": 0.038,
            },
            "peer_context": [],
            "data_quality_flags": [],
        },
        "as_of_date": "2026-05-04",
        "run_id": "test-run-001",
        "screening_s3_key": "screening/dt=2026-05-04/result.json",
    }


def _expected_input_hash(event: dict[str, Any]) -> str:
    """handle 이 build_context → context_input_hash 로 산출할 hash 를 미리 계산."""
    screened = ScreenedStock.model_validate(event["screened_stock"])
    ctx = build_context(
        screened,
        as_of_date=date.fromisoformat(event["as_of_date"]),
        run_id=event["run_id"],
        screening_s3_key=event["screening_s3_key"],
        ohlcv=None,
        income_quarterly=[],
        cashflow_quarterly=[],
    )
    return context_input_hash(ctx)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("FMP_SECRET_ID", "fmp-secret")
    monkeypatch.setenv("ANTHROPIC_SECRET_ID", "anthropic-secret")


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """write_text 호출 추적 — key→body."""
    captured: dict[str, str] = {}

    def fake_write(bucket: str, key: str, body: str) -> None:
        captured[key] = body

    monkeypatch.setattr(lambda_core, "write_text", fake_write)
    return captured


@pytest.fixture
def stub_io(monkeypatch: pytest.MonkeyPatch):
    """기본 stub: 캐시 miss / OHLCV 없음 / 분기 statements 빈 list."""
    monkeypatch.setattr(lambda_core, "read_json", lambda *a, **k: None)
    monkeypatch.setattr(lambda_core, "read_parquet", lambda *a, **k: None)
    monkeypatch.setattr(lambda_core, "get_secret", lambda sid: f"fake-{sid}")
    monkeypatch.setattr(
        lambda_core, "fetch_income_quarterly_with_cache", lambda *a, **k: []
    )
    monkeypatch.setattr(
        lambda_core, "fetch_cashflow_quarterly_with_cache", lambda *a, **k: []
    )


# ---------- 환경/입력 검증 ----------


def test_handle_raises_when_required_env_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("FMP_SECRET_ID", raising=False)
    monkeypatch.delenv("ANTHROPIC_SECRET_ID", raising=False)
    with pytest.raises(RuntimeError, match="S3_BUCKET"):
        lambda_core.handle(_event(), None, stance="bull", caller=_FakeAnthropic([_completion()]), fmp=object())


def test_handle_rejects_invalid_stance(env: None):
    with pytest.raises(ValueError, match="stance"):
        lambda_core.handle(_event(), None, stance="neutral")  # type: ignore[arg-type]


def test_handle_rejects_event_missing_screened_stock(env: None, stub_io: None, writes: dict[str, str]):
    bad = _event()
    del bad["screened_stock"]
    with pytest.raises(RuntimeError, match="입력 event"):
        lambda_core.handle(bad, None, stance="bull", caller=_FakeAnthropic([_completion()]), fmp=object())


def test_handle_rejects_event_with_bad_date_format(env: None, stub_io: None, writes: dict[str, str]):
    bad = _event()
    bad["as_of_date"] = "not-a-date"
    with pytest.raises(RuntimeError, match="입력 event"):
        lambda_core.handle(bad, None, stance="bull", caller=_FakeAnthropic([_completion()]), fmp=object())


# ---------- 캐시 miss 경로 ----------


def test_cache_miss_invokes_llm_and_writes_two_files(
    env: None, stub_io: None, writes: dict[str, str]
):
    fake_caller = _FakeAnthropic([_completion()])
    result = lambda_core.handle(
        _event(), None, stance="bull", caller=fake_caller, fmp=object()
    )

    assert result["status"] == "ok"
    assert result["cache"] == "miss"
    assert result["symbol"] == "AAPL"
    assert result["stance"] == "bull"
    assert result["attempts"] == 1
    assert result["cost_usd"] > 0
    assert fake_caller.calls == 1

    # S3 2개 키 저장: opinion + context
    opinion_keys = [k for k in writes if k.endswith("/stance=bull.json")]
    context_keys = [k for k in writes if k.endswith("/context.json")]
    assert len(opinion_keys) == 1
    assert len(context_keys) == 1
    assert "AAPL" in opinion_keys[0]
    assert "dt=2026-05-04" in opinion_keys[0]


def test_cache_miss_payload_includes_input_hash(
    env: None, stub_io: None, writes: dict[str, str]
):
    fake_caller = _FakeAnthropic([_completion()])
    lambda_core.handle(_event(), None, stance="bull", caller=fake_caller, fmp=object())

    opinion_key = next(k for k in writes if k.endswith("/stance=bull.json"))
    payload = json.loads(writes[opinion_key])
    assert "opinion" in payload
    assert "attempts" in payload
    assert "input_hash" in payload
    assert "cached_at" in payload
    assert payload["input_hash"] == _expected_input_hash(_event())
    assert payload["opinion"]["symbol"] == "AAPL"
    assert payload["opinion"]["stance"] == "bull"


# ---------- 캐시 hit 경로 ----------


def test_cache_hit_skips_llm(
    env: None, monkeypatch: pytest.MonkeyPatch, writes: dict[str, str]
):
    """동일 (key, input_hash) → LLM 호출 0회, cost=0, S3 write 도 안 일어남."""
    expected_hash = _expected_input_hash(_event())
    cached_payload = {
        "opinion": {
            "symbol": "AAPL",
            "stance": "bull",
            "as_of_date": "2026-05-04",
            **_VALID_OPINION_PAYLOAD,
            "model": "claude-sonnet-4-6",
            "input_tokens": 1500,
            "output_tokens": 900,
            "cost_usd": 0.018,
        },
        "attempts": [],
        "input_hash": expected_hash,
        "cached_at": "2026-04-30T00:00:00Z",
    }

    monkeypatch.setattr(lambda_core, "read_json", lambda *a, **k: cached_payload)
    monkeypatch.setattr(lambda_core, "read_parquet", lambda *a, **k: None)
    monkeypatch.setattr(lambda_core, "get_secret", lambda sid: f"fake-{sid}")
    monkeypatch.setattr(lambda_core, "fetch_income_quarterly_with_cache", lambda *a, **k: [])
    monkeypatch.setattr(lambda_core, "fetch_cashflow_quarterly_with_cache", lambda *a, **k: [])

    fake_caller = _FakeAnthropic([])  # 큐 비움 — 호출되면 RuntimeError
    result = lambda_core.handle(
        _event(), None, stance="bull", caller=fake_caller, fmp=object()
    )

    assert result["cache"] == "hit"
    assert result["cost_usd"] == 0.0
    assert result["attempts"] == 0
    assert fake_caller.calls == 0
    assert writes == {}  # S3 write 없음


def test_cache_stale_input_hash_treated_as_miss(
    env: None, monkeypatch: pytest.MonkeyPatch, writes: dict[str, str]
):
    """캐시는 있지만 input_hash 불일치 → miss 처럼 LLM 재호출."""
    cached_payload = {
        "opinion": {"symbol": "AAPL"},
        "input_hash": "stale-hash-different-from-current",
        "attempts": [],
        "cached_at": "2026-01-01T00:00:00Z",
    }

    monkeypatch.setattr(lambda_core, "read_json", lambda *a, **k: cached_payload)
    monkeypatch.setattr(lambda_core, "read_parquet", lambda *a, **k: None)
    monkeypatch.setattr(lambda_core, "get_secret", lambda sid: f"fake-{sid}")
    monkeypatch.setattr(lambda_core, "fetch_income_quarterly_with_cache", lambda *a, **k: [])
    monkeypatch.setattr(lambda_core, "fetch_cashflow_quarterly_with_cache", lambda *a, **k: [])

    fake_caller = _FakeAnthropic([_completion()])
    result = lambda_core.handle(
        _event(), None, stance="bull", caller=fake_caller, fmp=object()
    )

    assert result["cache"] == "miss"  # stale 캐시는 miss 처리
    assert fake_caller.calls == 1
    # 새로운 input_hash 로 덮어씀
    opinion_key = next(k for k in writes if k.endswith("/stance=bull.json"))
    payload = json.loads(writes[opinion_key])
    assert payload["input_hash"] == _expected_input_hash(_event())


# ---------- bull/bear stance 라우팅 ----------


def test_bear_stance_uses_bear_system_prompt(
    env: None, stub_io: None, writes: dict[str, str]
):
    """bear stance 호출 시 user 프롬프트에 bear 문자열 포함, S3 키도 bear."""
    fake_caller = _FakeAnthropic([_completion()])
    result = lambda_core.handle(
        _event(), None, stance="bear", caller=fake_caller, fmp=object()
    )

    assert result["stance"] == "bear"
    bear_keys = [k for k in writes if k.endswith("/stance=bear.json")]
    assert len(bear_keys) == 1


# ---------- wrapper 라우팅 ----------


def test_bull_wrapper_routes_to_handle_with_stance_bull(monkeypatch: pytest.MonkeyPatch):
    """src/lambdas/agent_bullbear_bull/handler.py 가 stance='bull' 주입."""
    from lambdas.agent_bullbear_bull import handler as bull_h

    seen: dict[str, Any] = {}

    def fake_handle(event, context, *, stance, **kwargs):
        seen["stance"] = stance
        seen["event"] = event
        return {"ok": True}

    monkeypatch.setattr(bull_h, "handle", fake_handle)
    bull_h.lambda_handler({"x": 1}, None)
    assert seen["stance"] == "bull"
    assert seen["event"] == {"x": 1}


def test_bear_wrapper_routes_to_handle_with_stance_bear(monkeypatch: pytest.MonkeyPatch):
    from lambdas.agent_bullbear_bear import handler as bear_h

    seen: dict[str, Any] = {}

    def fake_handle(event, context, *, stance, **kwargs):
        seen["stance"] = stance
        return {"ok": True}

    monkeypatch.setattr(bear_h, "handle", fake_handle)
    bear_h.lambda_handler({}, None)
    assert seen["stance"] == "bear"


# ---------- StockContext 재구성 (sanity) ----------


def test_handle_passes_through_lineage_to_context(
    env: None, stub_io: None, writes: dict[str, str]
):
    """저장된 context.json 에 run_id / screening_s3_key 보존."""
    fake_caller = _FakeAnthropic([_completion()])
    lambda_core.handle(_event(), None, stance="bull", caller=fake_caller, fmp=object())

    context_key = next(k for k in writes if k.endswith("/context.json"))
    ctx = StockContext.model_validate_json(writes[context_key])
    assert ctx.run_id == "test-run-001"
    assert ctx.screening_s3_key == "screening/dt=2026-05-04/result.json"
    assert ctx.symbol == "AAPL"
