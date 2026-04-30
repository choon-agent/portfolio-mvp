"""Bull/Bear agent.py 단위 테스트.

설계 근거: docs/02-bull-bear.md §3, §6, §7

LLM 호출은 FakeAnthropicClient 로 모킹 — 실제 Anthropic API 호출 없음.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import pytest

from agents.bull_bear.agent import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_PRIMARY_MODEL,
    AgentConfig,
    BullBearAgentError,
    CallAttempt,
    RawCompletion,
    _compute_cost,
    _parse_opinion,
    _strip_json_fence,
    _system_prompt,
    _user_prompt,
    context_input_hash,
    run_bullbear_agent,
)
from agents.bull_bear.schemas import (
    FundamentalsTimeseries,
    PriceSummary,
    StockContext,
)


# ---------- FakeAnthropicClient ----------


@dataclass
class FakeCall:
    model: str
    system: str
    user: str
    max_tokens: int
    temperature: float


class FakeAnthropicClient:
    """사전 정의된 응답 큐를 순서대로 반환. 예외도 큐에 넣어 실패 시뮬레이션."""

    def __init__(self, responses: list[RawCompletion | Exception]) -> None:
        self._queue = list(responses)
        self.calls: list[FakeCall] = []

    def call(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
    ) -> RawCompletion:
        self.calls.append(
            FakeCall(
                model=model,
                system=system,
                user=user,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        )
        if not self._queue:
            raise RuntimeError("FakeAnthropicClient: 응답 큐 소진")
        nxt = self._queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


# ---------- 픽스처 ----------


def _ctx(**overrides: object) -> StockContext:
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


def _valid_payload() -> dict[str, object]:
    """LLM 이 채우는 영역만 — 메타(symbol/stance/...) 는 agent 가 주입."""
    return {
        "summary": "Strong fundamentals with momentum tailwind.",
        "arguments": [
            {"claim": "Revenue growth steady", "evidence": "Revenue +12% YoY", "confidence": "high"},
            {"claim": "Margin expansion", "evidence": "Operating margin +200bp", "confidence": "medium"},
            {"claim": "FCF strong", "evidence": "FCF Yield 4%", "confidence": "medium"},
        ],
        "key_risks_to_thesis": ["Multiple compression if rates rise"],
    }


def _completion(payload: dict[str, object] | str, *, in_tok: int = 3200, out_tok: int = 580) -> RawCompletion:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return RawCompletion(text=text, input_tokens=in_tok, output_tokens=out_tok)


# ---------- 프롬프트 로딩 ----------


def test_system_prompt_loads_per_stance():
    bull = _system_prompt("bull")
    bear = _system_prompt("bear")
    assert "bull" in bull.lower()
    assert "bear" in bear.lower()
    assert bull != bear


def test_user_prompt_substitutes_all_placeholders():
    user = _user_prompt(_ctx(), "bull")
    assert "AAPL" in user
    assert "2026-05-04" in user
    assert "bull" in user
    # raw placeholder 토큰이 남아 있으면 안 됨
    assert "{context}" not in user
    assert "{stance}" not in user
    assert "{symbol}" not in user
    assert "{as_of_date}" not in user


def test_user_prompt_safe_against_braces_in_company_name():
    """회사명에 {/} 가 들어 있어도 KeyError 없이 치환."""
    ctx = _ctx(company_name="Strange {Format} Inc.")
    user = _user_prompt(ctx, "bull")
    assert "Strange {Format} Inc." in user


# ---------- _strip_json_fence ----------


def test_strip_json_fence_with_fence():
    text = "preamble\n```json\n{\"a\": 1}\n```\ntrailing"
    assert _strip_json_fence(text) == '{"a": 1}'


def test_strip_json_fence_without_fence():
    assert _strip_json_fence('  {"a": 1}  ') == '{"a": 1}'


def test_strip_json_fence_with_unlabeled_fence():
    text = "```\n{\"a\": 1}\n```"
    assert _strip_json_fence(text) == '{"a": 1}'


# ---------- _parse_opinion ----------


def test_parse_opinion_injects_meta_fields():
    opinion = _parse_opinion(
        json.dumps(_valid_payload()),
        symbol="AAPL",
        stance="bull",
        as_of_date=date(2026, 5, 4),
        model="claude-sonnet-4-6",
        input_tokens=3200,
        output_tokens=580,
        cost_usd=0.018,
    )
    assert opinion.symbol == "AAPL"
    assert opinion.stance == "bull"
    assert opinion.as_of_date == date(2026, 5, 4)
    assert opinion.model == "claude-sonnet-4-6"
    assert opinion.cost_usd == 0.018


def test_parse_opinion_overrides_llm_meta_fields():
    """LLM 이 잘못된 stance/symbol 등을 적어도 호출 측 값이 우선."""
    payload = _valid_payload()
    payload["symbol"] = "WRONG"
    payload["stance"] = "bear"  # bull 호출인데 LLM 이 bear 라고 적은 케이스
    opinion = _parse_opinion(
        json.dumps(payload),
        symbol="AAPL",
        stance="bull",
        as_of_date=date(2026, 5, 4),
        model="m",
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.0,
    )
    assert opinion.symbol == "AAPL"
    assert opinion.stance == "bull"


def test_parse_opinion_raises_on_invalid_json():
    with pytest.raises(json.JSONDecodeError):
        _parse_opinion(
            "not a json",
            symbol="X",
            stance="bull",
            as_of_date=date(2026, 5, 4),
            model="m",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )


def test_parse_opinion_raises_on_non_object_json():
    with pytest.raises(ValueError, match="JSON object"):
        _parse_opinion(
            "[1, 2, 3]",
            symbol="X",
            stance="bull",
            as_of_date=date(2026, 5, 4),
            model="m",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )


def test_parse_opinion_raises_on_schema_violation():
    """arguments 가 2개 (3 미만) → ValidationError."""
    from pydantic import ValidationError

    bad = _valid_payload()
    bad["arguments"] = bad["arguments"][:2]  # type: ignore[index]
    with pytest.raises(ValidationError):
        _parse_opinion(
            json.dumps(bad),
            symbol="X",
            stance="bull",
            as_of_date=date(2026, 5, 4),
            model="m",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )


# ---------- _compute_cost ----------


def test_compute_cost_uses_pricing_table():
    cost = _compute_cost(
        "claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        pricing={"claude-sonnet-4-6": {"input": 3.0, "output": 15.0}},
    )
    assert cost == pytest.approx(18.0)


def test_compute_cost_unknown_model_returns_zero():
    cost = _compute_cost("unknown", input_tokens=1000, output_tokens=1000, pricing={})
    assert cost == 0.0


def test_compute_cost_zero_tokens():
    cost = _compute_cost(
        "claude-sonnet-4-6",
        input_tokens=0,
        output_tokens=0,
        pricing={"claude-sonnet-4-6": {"input": 3.0, "output": 15.0}},
    )
    assert cost == 0.0


# ---------- context_input_hash ----------


def test_context_input_hash_is_deterministic():
    a = context_input_hash(_ctx())
    b = context_input_hash(_ctx())
    assert a == b
    assert len(a) == 64  # SHA-256 hex


def test_context_input_hash_changes_with_content():
    a = context_input_hash(_ctx())
    b = context_input_hash(_ctx(composite_score=2.0))
    assert a != b


def test_context_input_hash_changes_with_lineage():
    """run_id 가 바뀌면 hash 도 변경 — 다른 실행 = 다른 캐시 키."""
    a = context_input_hash(_ctx(run_id="r1"))
    b = context_input_hash(_ctx(run_id="r2"))
    assert a != b


# ---------- run_bullbear_agent (정상 경로) ----------


def test_run_agent_success_on_first_attempt():
    fake = FakeAnthropicClient([_completion(_valid_payload())])
    result = run_bullbear_agent(_ctx(), "bull", caller=fake)
    assert result.opinion.symbol == "AAPL"
    assert result.opinion.stance == "bull"
    assert len(result.attempts) == 1
    assert result.attempts[0].succeeded
    assert result.attempts[0].stage == "primary"
    assert result.attempts[0].model == DEFAULT_PRIMARY_MODEL


def test_run_agent_passes_temperature_zero_by_default():
    fake = FakeAnthropicClient([_completion(_valid_payload())])
    run_bullbear_agent(_ctx(), "bull", caller=fake)
    assert fake.calls[0].temperature == 0.0


def test_run_agent_uses_stance_specific_system_prompt():
    fake = FakeAnthropicClient([_completion(_valid_payload())])
    run_bullbear_agent(_ctx(), "bear", caller=fake)
    # bear system 프롬프트가 들어갔는지 (한 줄 키워드 검증)
    assert "bear" in fake.calls[0].system.lower()


def test_run_agent_user_prompt_contains_context_markdown():
    fake = FakeAnthropicClient([_completion(_valid_payload())])
    run_bullbear_agent(_ctx(), "bull", caller=fake)
    # to_prompt_markdown 의 출력 헤더 — Apple 회사명 + as_of_date 가 user 에 포함
    assert "AAPL" in fake.calls[0].user
    assert "2026-05-04" in fake.calls[0].user


def test_run_agent_cost_in_attempt_uses_pricing_table():
    fake = FakeAnthropicClient([_completion(_valid_payload(), in_tok=1_000_000, out_tok=0)])
    result = run_bullbear_agent(
        _ctx(),
        "bull",
        caller=fake,
        pricing={DEFAULT_PRIMARY_MODEL: {"input": 3.0, "output": 15.0}},
    )
    assert result.attempts[0].cost_usd == pytest.approx(3.0)
    assert result.opinion.cost_usd == pytest.approx(3.0)


# ---------- run_bullbear_agent (재시도/폴백 사다리) ----------


def test_run_agent_retries_primary_on_invalid_json():
    fake = FakeAnthropicClient(
        [
            _completion("not a json"),  # primary 1차 — JSON parse 실패
            _completion(_valid_payload()),  # primary retry — 성공
        ]
    )
    result = run_bullbear_agent(_ctx(), "bull", caller=fake)
    assert len(result.attempts) == 2
    assert not result.attempts[0].succeeded
    assert result.attempts[0].stage == "primary"
    assert result.attempts[1].stage == "primary_retry"
    assert result.attempts[1].succeeded
    # 재시도도 같은 primary 모델 사용
    assert fake.calls[1].model == DEFAULT_PRIMARY_MODEL


def test_run_agent_falls_back_to_haiku_after_two_primary_failures():
    bad = _valid_payload()
    bad["arguments"] = bad["arguments"][:1]  # type: ignore[index]  # schema 위반
    fake = FakeAnthropicClient(
        [
            _completion(bad),  # primary 1차 — schema 실패
            _completion(bad),  # primary retry — 또 schema 실패
            _completion(_valid_payload()),  # fallback Haiku — 성공
        ]
    )
    result = run_bullbear_agent(_ctx(), "bull", caller=fake)
    assert len(result.attempts) == 3
    assert result.attempts[0].stage == "primary" and not result.attempts[0].succeeded
    assert result.attempts[1].stage == "primary_retry" and not result.attempts[1].succeeded
    assert result.attempts[2].stage == "fallback" and result.attempts[2].succeeded
    assert fake.calls[2].model == DEFAULT_FALLBACK_MODEL


def test_run_agent_handles_caller_exceptions_in_ladder():
    """caller 가 던진 예외도 사다리에서 흡수, 다음 단계로 진행."""
    fake = FakeAnthropicClient(
        [
            RuntimeError("network 5xx"),  # primary 1차 — 호출 자체 실패
            _completion(_valid_payload()),  # primary retry — 성공
        ]
    )
    result = run_bullbear_agent(_ctx(), "bull", caller=fake)
    assert len(result.attempts) == 2
    assert not result.attempts[0].succeeded
    assert "RuntimeError" in (result.attempts[0].error or "")
    assert result.attempts[1].succeeded


def test_run_agent_raises_when_all_attempts_fail():
    fake = FakeAnthropicClient(
        [
            _completion("garbage 1"),
            _completion("garbage 2"),
            _completion("garbage 3"),
        ]
    )
    with pytest.raises(BullBearAgentError) as exc_info:
        run_bullbear_agent(_ctx(), "bull", caller=fake)
    err = exc_info.value
    assert len(err.attempts) == 3
    assert all(not a.succeeded for a in err.attempts)


def test_run_agent_total_cost_aggregates_attempts():
    """모든 시도의 비용이 누적 — 실패 호출도 토큰 발생 시 비용 계산."""
    bad = _valid_payload()
    bad["arguments"] = bad["arguments"][:1]  # type: ignore[index]
    fake = FakeAnthropicClient(
        [
            _completion(bad, in_tok=1_000_000, out_tok=0),
            _completion(_valid_payload(), in_tok=1_000_000, out_tok=0),
        ]
    )
    result = run_bullbear_agent(
        _ctx(),
        "bull",
        caller=fake,
        pricing={DEFAULT_PRIMARY_MODEL: {"input": 3.0, "output": 15.0}},
    )
    # 두 번 호출 × 1M 입력 토큰 × $3/1M = $6 누적
    assert result.total_cost_usd == pytest.approx(6.0)


def test_run_agent_config_override():
    fake = FakeAnthropicClient([_completion(_valid_payload())])
    run_bullbear_agent(
        _ctx(),
        "bull",
        caller=fake,
        config=AgentConfig(
            primary_model="custom-primary",
            fallback_model="custom-fallback",
            max_tokens=2048,
            temperature=0.5,
        ),
    )
    assert fake.calls[0].model == "custom-primary"
    assert fake.calls[0].max_tokens == 2048
    assert fake.calls[0].temperature == 0.5


def test_run_agent_call_attempt_dataclass_immutable():
    """CallAttempt 는 frozen — 누적 후 외부 수정 차단."""
    a = CallAttempt(
        model="m", stage="primary", input_tokens=1, output_tokens=1, cost_usd=0.0, succeeded=True
    )
    with pytest.raises(Exception):
        a.succeeded = False  # type: ignore[misc]
