"""시나리오 agent 단위 테스트.

LLM 호출은 FakeAnthropicClient 로 모킹 — 실제 Anthropic API 호출 없음.
설계 근거: docs/03-scenario.md §3, §6, §9
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import pytest

from agents.bull_bear.agent import AgentConfig, RawCompletion
from agents.bull_bear.schemas import Argument, BullBearOpinion
from agents.scenario.agent import (
    ScenarioAgentError,
    _compute_cost,
    _system_prompt,
    _user_prompt,
    run_scenario_agent,
    scenario_input_hash,
)
from agents.scenario.schemas import ScenarioContext

AS_OF = date(2026, 5, 4)


# ---------- FakeAnthropicClient ----------


@dataclass
class FakeCall:
    model: str
    system: str
    user: str
    max_tokens: int
    temperature: float


class FakeAnthropicClient:
    """사전 정의 응답 큐를 순서대로 반환. 예외도 큐에 넣어 실패 시뮬레이션."""

    def __init__(self, responses: list[RawCompletion | Exception]) -> None:
        self._queue = list(responses)
        self.calls: list[FakeCall] = []

    def call(self, *, model, system, user, max_tokens, temperature) -> RawCompletion:
        self.calls.append(FakeCall(model, system, user, max_tokens, temperature))
        if not self._queue:
            raise RuntimeError("FakeAnthropicClient: 응답 큐 소진")
        nxt = self._queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


# ---------- 픽스처 ----------


def _bb(stance: str, symbol: str = "AAPL") -> BullBearOpinion:
    return BullBearOpinion(
        symbol=symbol, stance=stance, as_of_date=AS_OF, summary=f"{stance} s",  # type: ignore[arg-type]
        arguments=[Argument(claim="c", evidence="e", confidence="high")] * 3,
        key_risks_to_thesis=["r"], model="m",
        input_tokens=0, output_tokens=0, cost_usd=0.0,
    )


def _ctx(**overrides: object) -> ScenarioContext:
    base: dict[str, object] = {
        "symbol": "AAPL",
        "as_of_date": AS_OF,
        "bull_opinion": _bb("bull"),
        "bear_opinion": _bb("bear"),
        "current_price": 100.0,
        "ttm_eps": 10.0,
        "peer_pe": [25.0, 30.0],
        "return_52w_high": 0.3,
        "return_52w_low": -0.2,
        "run_id": "2026-05-04T00:00:00Z",
        "scenario_s3_key": "scenarios/dt=2026-05-04/symbol=AAPL.json",
        "bullbear_s3_keys": {"bull": "kb", "bear": "kr"},
    }
    base.update(overrides)
    return ScenarioContext(**base)  # type: ignore[arg-type]


def _trigger(metric: str = "revenue_yoy") -> dict[str, object]:
    return {
        "metric": metric, "direction": "less_than", "threshold": 5.0,
        "threshold_unit": "percent", "description": "metric below threshold pct",
    }


def _valid_payload(probs: tuple[float, float, float] = (0.4, 0.45, 0.15)) -> dict[str, object]:
    """LLM 이 채우는 영역(scenarios) 만 — 메타는 agent 가 주입."""
    pb, pba, pbe = probs
    return {
        "scenarios": [
            {"label": "bull", "probability": pb, "narrative": "bull case cites Bull margin evidence", "invalidation_trigger": _trigger()},
            {"label": "base", "probability": pba, "narrative": "base case cites steady fundamentals", "invalidation_trigger": _trigger("eps_yoy")},
            {"label": "bear", "probability": pbe, "narrative": "bear case cites Bear demand risk", "invalidation_trigger": _trigger("fcf_yoy")},
        ]
    }


def _completion(payload: dict[str, object] | str, *, in_tok: int = 3300, out_tok: int = 500) -> RawCompletion:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return RawCompletion(text=text, input_tokens=in_tok, output_tokens=out_tok)


# ---------- 프롬프트 로딩 ----------


def test_system_prompt_loads() -> None:
    assert "multi-scenario" in _system_prompt()


def test_user_prompt_substitutes_placeholders() -> None:
    user = _user_prompt(_ctx())
    assert "AAPL" in user
    assert "2026-05-04" in user
    assert "{context}" not in user
    assert "{symbol}" not in user
    # context markdown 이 삽입됨
    assert "## Bull Opinion" in user


# ---------- happy path ----------


def test_run_happy_path() -> None:
    fake = FakeAnthropicClient([_completion(_valid_payload())])
    result = run_scenario_agent(_ctx(), caller=fake)
    assert len(result.opinion.scenarios) == 3
    assert len(result.attempts) == 1
    assert result.attempts[0].succeeded
    assert result.attempts[0].stage == "primary"
    assert len(fake.calls) == 1


def test_meta_injected_not_from_llm() -> None:
    fake = FakeAnthropicClient([_completion(_valid_payload(), in_tok=3300, out_tok=500)])
    op = run_scenario_agent(_ctx(), caller=fake).opinion
    assert op.symbol == "AAPL"
    assert op.as_of_date == AS_OF
    assert op.model == "claude-sonnet-4-6"
    assert op.input_tokens == 3300
    assert op.output_tokens == 500
    # cost = 3300*3/1e6 + 500*15/1e6 = 0.0099 + 0.0075 = 0.0174
    assert op.cost_usd == pytest.approx(0.0174)


def test_json_fence_stripped() -> None:
    fenced = "```json\n" + json.dumps(_valid_payload()) + "\n```"
    fake = FakeAnthropicClient([_completion(fenced)])
    result = run_scenario_agent(_ctx(), caller=fake)
    assert len(result.opinion.scenarios) == 3


# ---------- 사다리 ----------


def test_primary_exception_then_retry_succeeds() -> None:
    fake = FakeAnthropicClient([
        RuntimeError("5xx timeout"),
        _completion(_valid_payload()),
    ])
    result = run_scenario_agent(_ctx(), caller=fake)
    assert result.opinion is not None
    assert len(result.attempts) == 2
    assert not result.attempts[0].succeeded
    assert result.attempts[1].stage == "primary_retry"
    assert result.attempts[1].succeeded


def test_validation_fail_falls_back_to_haiku() -> None:
    bad = _valid_payload((0.4, 0.4, 0.4))  # 확률 합 1.2 → ValidationError
    fake = FakeAnthropicClient([
        _completion(bad),
        _completion(bad),
        _completion(_valid_payload()),  # Haiku 성공
    ])
    result = run_scenario_agent(_ctx(), caller=fake)
    assert result.attempts[2].stage == "fallback"
    assert result.attempts[2].model == "claude-haiku-4-5-20251001"
    assert result.attempts[2].succeeded


def test_all_fail_raises() -> None:
    bad = _completion("not json at all")
    fake = FakeAnthropicClient([bad, bad, bad])
    with pytest.raises(ScenarioAgentError) as exc:
        run_scenario_agent(_ctx(), caller=fake)
    assert len(exc.value.attempts) == 3
    assert all(not a.succeeded for a in exc.value.attempts)


def test_total_cost_sums_attempts() -> None:
    fake = FakeAnthropicClient([
        _completion("bad", in_tok=100, out_tok=10),  # 실패도 비용 누적
        _completion(_valid_payload(), in_tok=3300, out_tok=500),
    ])
    result = run_scenario_agent(_ctx(), caller=fake)
    assert result.total_cost_usd == pytest.approx(
        _compute_cost("claude-sonnet-4-6", 100, 10, {"claude-sonnet-4-6": {"input": 3.0, "output": 15.0}})
        + 0.0174
    )


# ---------- scenario_input_hash (docs §6.2) ----------


def test_hash_deterministic() -> None:
    assert scenario_input_hash(_ctx()) == scenario_input_hash(_ctx())


def test_hash_excludes_lineage() -> None:
    # lineage 만 다르면 같은 hash (프롬프트 미노출 → 캐시 키 무관)
    h1 = scenario_input_hash(_ctx())
    h2 = scenario_input_hash(_ctx(
        run_id="DIFFERENT",
        scenario_s3_key="other/key.json",
        bullbear_s3_keys={"bull": "x", "bear": "y"},
        data_quality_flags=["some_flag"],
    ))
    assert h1 == h2


def test_hash_changes_on_price() -> None:
    assert scenario_input_hash(_ctx()) != scenario_input_hash(_ctx(current_price=150.0))


def test_hash_changes_on_opinion() -> None:
    other = _bb("bull")
    other = other.model_copy(update={"summary": "different thesis entirely"})
    assert scenario_input_hash(_ctx()) != scenario_input_hash(_ctx(bull_opinion=other))


# ---------- config override ----------


def test_config_override_max_tokens() -> None:
    fake = FakeAnthropicClient([_completion(_valid_payload())])
    run_scenario_agent(_ctx(), caller=fake, config=AgentConfig(max_tokens=4096))
    assert fake.calls[0].max_tokens == 4096
    assert fake.calls[0].temperature == 0.0
