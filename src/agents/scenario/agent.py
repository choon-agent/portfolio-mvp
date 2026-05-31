"""시나리오 에이전트 단일 호출 진입점.

설계 근거: docs/03-scenario.md §3, §5, §6, §9

이 모듈의 역할:
1. 프롬프트 로딩 (scenario_system + scenario_user 템플릿 치환)
2. Anthropic API 호출 (Protocol 추상화 — Lambda 핸들러가 SDK 어댑터 주입)
3. JSON 파싱 + Pydantic 검증 (ScenarioOpinion 형태 강제)
4. 재시도/폴백 사다리: Sonnet → Sonnet retry → Haiku (Bull/Bear 와 동일)
5. 토큰/비용 산출 + 호출 로그 (CLAUDE.md 로깅 규칙)
6. scenario_input_hash — 결정성 캐시 키 (lineage 제외, docs §6.2)

LLM 추론(시나리오·확률·트리거) 만 담당 — 가격 산정은 pricing.py 의 결정적
산식 (docs §1.4 분리 원칙). 본 모듈은 ScenarioOpinion 까지, ExpectedReturn 은
호출 측(#9 lambda_core)이 compute_expected_return 으로 산출.

Bull/Bear 와 공유: RawCompletion/AnthropicCaller/AgentConfig/CallAttempt/
DEFAULT_PRICING 는 `agents.bull_bear.agent` 의 generic 인프라 (SDK 어댑터
`AnthropicSDKCaller` 도 종목 무관이라 그대로 재사용 — docs §3.1).

단위 테스트는 FakeAnthropicClient 로 모킹 (CLAUDE.md "LLM 호출 함수는 목 테스트").
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from agents.bull_bear.agent import (
    DEFAULT_PRICING,
    AgentConfig,
    AnthropicCaller,
    CallAttempt,
)
from agents.scenario.context_builder import to_prompt_markdown
from agents.scenario.schemas import ScenarioContext, ScenarioOpinion

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
PROMPT_PURPOSE = "scenario"

# scenario_input_hash 에서 제외할 lineage 필드 (docs §6.2 — LLM 이 보는 프롬프트엔
# 미노출이므로 캐시 키에서도 제외 → "같은 프롬프트 → 같은 hash → cache hit").
_HASH_EXCLUDE = {
    "run_id",
    "scenario_s3_key",
    "bullbear_s3_keys",
    "data_quality_flags",
}

# Bull/Bear 와 동일 사다리: primary 1차 → primary 재시도 → fallback (Haiku)
_LADDER: tuple[tuple[str, str], ...] = (
    ("primary", "primary"),
    ("primary", "primary_retry"),
    ("fallback", "fallback"),
)


# ---------- 호출 결과 ----------


@dataclass(frozen=True)
class ScenarioCallResult:
    """run_scenario_agent 의 정상 반환. attempts 는 모든 시도 포함."""

    opinion: ScenarioOpinion
    attempts: list[CallAttempt] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return sum(a.cost_usd for a in self.attempts)


class ScenarioAgentError(Exception):
    """모든 재시도/폴백 후에도 검증된 출력을 못 얻음. attempts 는 디버깅용."""

    def __init__(self, message: str, attempts: list[CallAttempt]):
        super().__init__(message)
        self.attempts = attempts


# ---------- 프롬프트 ----------


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _system_prompt() -> str:
    return _load_prompt("scenario_system.md")


def _user_prompt(ctx: ScenarioContext) -> str:
    """scenario_user.md 의 placeholder 3개 치환.

    str.format 대신 직접 replace — context markdown/회사명에 우연히 중괄호가
    들어 있어도 안전 (KeyError·IndexError 방지).
    """
    template = _load_prompt("scenario_user.md")
    return (
        template.replace("{context}", to_prompt_markdown(ctx))
        .replace("{symbol}", ctx.symbol)
        .replace("{as_of_date}", ctx.as_of_date.isoformat())
    )


# ---------- JSON 추출 + 검증 ----------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _strip_json_fence(text: str) -> str:
    """```json ... ``` 펜스 안의 본문만, 없으면 원문 그대로."""
    m = _FENCE_RE.search(text)
    return (m.group(1) if m else text).strip()


def _parse_opinion(
    text: str,
    *,
    symbol: str,
    as_of_date: date,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> ScenarioOpinion:
    """LLM 응답 텍스트 → 검증된 ScenarioOpinion.

    LLM 이 채우는 영역은 `scenarios` 만; 메타(symbol/as_of_date/model/tokens/
    cost)는 호출 측이 주입해 LLM 의 자유 작성 영역에서 배제.
    """
    cleaned = _strip_json_fence(text)
    raw = json.loads(cleaned)  # JSONDecodeError 가능 — run_scenario_agent 가 잡음
    if not isinstance(raw, dict):
        raise ValueError(f"LLM 응답이 JSON object 가 아님: {type(raw).__name__}")

    raw["symbol"] = symbol
    raw["as_of_date"] = as_of_date.isoformat()
    raw["model"] = model
    raw["input_tokens"] = input_tokens
    raw["output_tokens"] = output_tokens
    raw["cost_usd"] = cost_usd

    return ScenarioOpinion.model_validate(raw)


# ---------- 비용 ----------


def _compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    pricing: dict[str, dict[str, float]],
) -> float:
    """모델별 단가로 USD 환산. 미등록 모델은 0 — 가시성 위해 경고만."""
    rates = pricing.get(model)
    if rates is None:
        logger.warning("pricing 에 %s 단가 없음 — 비용 0 으로 기록", model)
        return 0.0
    return (
        input_tokens * rates["input"] / 1_000_000
        + output_tokens * rates["output"] / 1_000_000
    )


# ---------- 캐시 키 (Lambda 핸들러 #9 가 사용, docs §6.2) ----------


def scenario_input_hash(ctx: ScenarioContext) -> str:
    """ScenarioContext 의 결정적 SHA-256 해시 (lineage 제외).

    LLM 이 실제로 소비하는 입력(Bull/Bear 의견 본문 + 가격 컨텍스트)만 해시 —
    run_id/s3_keys/data_quality_flags 는 프롬프트 미노출이므로 제외. 동일 입력
    → 동일 hash → lambda_core 가 LLM 호출 생략 (cost=0). 재실행 폭주 방지 +
    재현성 (docs §6.2, CHARTER 2순위).
    """
    payload = ctx.model_dump_json(exclude=_HASH_EXCLUDE).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ---------- 메인 ----------


def run_scenario_agent(
    ctx: ScenarioContext,
    *,
    caller: AnthropicCaller,
    config: AgentConfig | None = None,
    pricing: dict[str, dict[str, float]] | None = None,
    purpose: str = PROMPT_PURPOSE,
) -> ScenarioCallResult:
    """단일 종목 시나리오 LLM 호출. 재시도/폴백 사다리 적용.

    사다리: primary(Sonnet) 1차 → primary 재시도 → fallback(Haiku). 모든 호출
    실패 시 ScenarioAgentError 에 attempts 포함 raise (docs §9).

    인자:
        ctx: ScenarioContext (Bull/Bear 의견 2개 + 가격 컨텍스트).
        caller: Anthropic 호출 어댑터 (Lambda 는 AnthropicSDKCaller, 테스트는 Fake).
        config: 모델·temperature·max_tokens. None 이면 기본값 (Bull/Bear 와 동일).
        pricing: USD/1M 단가. None 이면 DEFAULT_PRICING.
        purpose: 로그의 purpose 필드.

    로그: 시도마다 1행 — stage/model/symbol/tokens/cost/schema_valid.
    """
    cfg = config or AgentConfig()
    px = pricing or DEFAULT_PRICING
    system = _system_prompt()
    user = _user_prompt(ctx)

    attempts: list[CallAttempt] = []

    for which, stage in _LADDER:
        model = cfg.primary_model if which == "primary" else cfg.fallback_model

        # 1) 호출
        try:
            raw = caller.call(
                model=model,
                system=system,
                user=user,
                max_tokens=cfg.max_tokens,
                temperature=cfg.temperature,
            )
        except Exception as exc:  # noqa: BLE001 — caller 가 던지는 모든 예외 흡수
            attempts.append(
                CallAttempt(
                    model=model, stage=stage, input_tokens=0, output_tokens=0,
                    cost_usd=0.0, succeeded=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            logger.warning(
                json.dumps({
                    "purpose": purpose, "stage": stage, "symbol": ctx.symbol,
                    "model": model, "error": f"{type(exc).__name__}: {exc}",
                })
            )
            continue

        # 2) 비용
        cost = _compute_cost(model, raw.input_tokens, raw.output_tokens, px)

        # 3) 파싱 + 검증
        try:
            opinion = _parse_opinion(
                raw.text, symbol=ctx.symbol, as_of_date=ctx.as_of_date,
                model=model, input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens, cost_usd=cost,
            )
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            attempts.append(
                CallAttempt(
                    model=model, stage=stage, input_tokens=raw.input_tokens,
                    output_tokens=raw.output_tokens, cost_usd=cost,
                    succeeded=False, error=f"{type(exc).__name__}: {exc}",
                )
            )
            logger.warning(
                json.dumps({
                    "purpose": purpose, "stage": stage, "symbol": ctx.symbol,
                    "model": model, "input_tokens": raw.input_tokens,
                    "output_tokens": raw.output_tokens, "cost_usd": cost,
                    "schema_valid": False, "error": f"{type(exc).__name__}: {exc}",
                })
            )
            continue

        # 4) 성공
        attempts.append(
            CallAttempt(
                model=model, stage=stage, input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens, cost_usd=cost, succeeded=True,
            )
        )
        logger.info(
            json.dumps({
                "purpose": purpose, "stage": stage, "symbol": ctx.symbol,
                "model": model, "input_tokens": raw.input_tokens,
                "output_tokens": raw.output_tokens, "cost_usd": cost,
                "schema_valid": True,
                "scenarios_probabilities": [
                    s.probability for s in opinion.scenarios
                ],
            })
        )
        return ScenarioCallResult(opinion=opinion, attempts=attempts)

    raise ScenarioAgentError(
        f"all {len(_LADDER)} attempts failed for {ctx.symbol}", attempts
    )
