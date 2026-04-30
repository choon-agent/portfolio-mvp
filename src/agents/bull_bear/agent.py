"""Bull/Bear 에이전트 단일 호출 진입점.

설계 근거: docs/02-bull-bear.md §3, §4, §5, §6, §7, §9

이 모듈의 역할:
1. 프롬프트 로딩 (system + user 템플릿 치환)
2. Anthropic API 호출 (Protocol 추상화 — Lambda 핸들러가 SDK 어댑터 주입)
3. JSON 파싱 + Pydantic 검증 (출력 형태 강제)
4. 재시도/폴백 사다리: Sonnet → Sonnet retry → Haiku
5. 토큰/비용 산출 + 호출 로그 (CLAUDE.md 로깅 규칙)
6. 결정성 보조: temperature=0 기본값, context_input_hash 노출 (캐시 키 재료)

LLM 호출은 일어나지만 SDK 직접 의존은 없음 — Protocol AnthropicCaller 를
주입식으로 받음. 단위 테스트는 FakeAnthropicClient 로 모킹 (CLAUDE.md "LLM
호출 함수는 목 테스트 작성").

I/O 책임 분리 (CLAUDE.md):
- 본 모듈: 프롬프트 합성 + LLM 호출 + 검증 + 재시도
- Lambda 핸들러(#7): API 키 secrets 조회, S3 입출력, 캐시 hit/miss, SDK 어댑터 생성

결정성 (docs §10 운영 동등성 시나리오):
- temperature=0 기본 — Anthropic 가 near-deterministic
- to_prompt_markdown 결정성 + BullBearOpinion Pydantic 검증으로 *형태* 100% 보장
- *내용* 동등성은 Lambda 핸들러의 S3 출력 캐시(#7) 와 결합해 (symbol, as_of_date,
  stance, input_hash) 동일 시 LLM 호출 자체를 생략 → 100% 결정적
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from agents.bull_bear.context_builder import to_prompt_markdown
from agents.bull_bear.schemas import BullBearOpinion, StockContext

logger = logging.getLogger(__name__)

# ---------- 상수 ----------

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

DEFAULT_PRIMARY_MODEL = "claude-sonnet-4-6"
DEFAULT_FALLBACK_MODEL = "claude-haiku-4-5-20251001"

# USD per 1M tokens. CHARTER §3.3 기준 (2026-04 시점). 실제 단가 변동 가능 —
# 호출 측이 override 가능하도록 외부 인자로도 받음.
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
}

# 출력 실측: 골든 케이스 8회 평균 ~900 토큰, 최대 1024(잘림). 1024 는 too tight —
# arguments 5 + 각 evidence 길고 + key_risks 3 자세히 쓰면 1000 초과 빈번.
# 2048 로 여유. 비용은 *실제* 사용량만 청구되므로 상한 확대는 비용 증가 X.
DEFAULT_MAX_TOKENS = 2048

PROMPT_PURPOSE = "bullbear"


# ---------- 설정 ----------


@dataclass(frozen=True)
class AgentConfig:
    """단일 호출 설정. 호출 측이 필요시 override.

    temperature=0 는 결정성 우선 — docs §10 "동일 질의 동일 답변" 정책의
    출발점. Anthropic 은 temperature=0 에서도 100% 결정적은 아니지만 (`near-
    deterministic`), 본 모듈 레벨에서 가능한 최선.
    """

    primary_model: str = DEFAULT_PRIMARY_MODEL
    fallback_model: str = DEFAULT_FALLBACK_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = 0.0


# ---------- 외부 SDK 추상화 ----------


@dataclass(frozen=True)
class RawCompletion:
    """Anthropic API 응답을 모듈 내부에서 다룰 평면 표현.

    SDK 의존 없이 호출 측 어댑터가 채움 — text 는 LLM 응답 본문 (보통 JSON),
    input_tokens/output_tokens 는 API usage 필드.
    """

    text: str
    input_tokens: int
    output_tokens: int


class AnthropicCaller(Protocol):
    """Anthropic 호출 인터페이스. Lambda 핸들러가 실제 SDK 어댑터,
    단위 테스트는 FakeAnthropicClient 를 주입.
    """

    def call(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
    ) -> RawCompletion: ...


# ---------- 호출 결과 ----------


@dataclass(frozen=True)
class CallAttempt:
    """단일 시도의 메트릭 (성공/실패 모두 보존 — 누적 비용·디버깅용)."""

    model: str
    stage: str  # "primary", "primary_retry", "fallback"
    input_tokens: int
    output_tokens: int
    cost_usd: float
    succeeded: bool
    error: str | None = None


@dataclass(frozen=True)
class CallResult:
    """run_bullbear_agent 의 정상 반환. attempts 는 모든 시도 포함."""

    opinion: BullBearOpinion
    attempts: list[CallAttempt] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return sum(a.cost_usd for a in self.attempts)


class BullBearAgentError(Exception):
    """모든 재시도/폴백 후에도 검증된 출력을 못 얻음. attempts 는 디버깅용."""

    def __init__(self, message: str, attempts: list[CallAttempt]):
        super().__init__(message)
        self.attempts = attempts


# ---------- 프롬프트 ----------


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _system_prompt(stance: Literal["bull", "bear"]) -> str:
    return _load_prompt(f"{stance}_system.md")


def _user_prompt(ctx: StockContext, stance: Literal["bull", "bear"]) -> str:
    """bullbear_user.md 의 4개 placeholder 를 치환.

    str.format 대신 직접 replace — context markdown 또는 회사명에 우연히
    중괄호가 들어 있어도 안전 (KeyError·IndexError 방지).
    """
    template = _load_prompt("bullbear_user.md")
    return (
        template.replace("{context}", to_prompt_markdown(ctx))
        .replace("{stance}", stance)
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
    stance: Literal["bull", "bear"],
    as_of_date: date,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> BullBearOpinion:
    """LLM 응답 텍스트 → 검증된 BullBearOpinion.

    LLM 이 채울 수 있는 필드는 본문(claim/evidence/confidence 등) 만; 메타
    필드(symbol/stance/as_of_date/model/tokens/cost)는 호출 측이 주입해
    LLM 이 잘못 쓰는 케이스(예: stance=bull 인데 출력에 bear 적음)를 차단.
    """
    cleaned = _strip_json_fence(text)
    raw = json.loads(cleaned)  # JSONDecodeError 가능 — 호출 측이 잡음
    if not isinstance(raw, dict):
        raise ValueError(f"LLM 응답이 JSON object 가 아님: {type(raw).__name__}")

    # 메타 필드 강제 주입 (LLM 의 자유 작성 영역 아님)
    raw["symbol"] = symbol
    raw["stance"] = stance
    raw["as_of_date"] = as_of_date.isoformat()
    raw["model"] = model
    raw["input_tokens"] = input_tokens
    raw["output_tokens"] = output_tokens
    raw["cost_usd"] = cost_usd

    return BullBearOpinion.model_validate(raw)


# ---------- 비용 ----------


def _compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    pricing: dict[str, dict[str, float]],
) -> float:
    """모델별 단가로 USD 환산. 미등록 모델은 0 — 호출 측 가시성 위해 경고만."""
    rates = pricing.get(model)
    if rates is None:
        logger.warning("pricing 에 %s 단가 없음 — 비용 0 으로 기록", model)
        return 0.0
    return input_tokens * rates["input"] / 1_000_000 + output_tokens * rates["output"] / 1_000_000


# ---------- 캐시 키 (Lambda 핸들러 #7 가 사용) ----------


def context_input_hash(ctx: StockContext) -> str:
    """StockContext 의 결정적 SHA-256 해시.

    Pydantic v2 의 model_dump_json 은 필드 순서 보존 — 동일 객체 → 동일 문자열.
    Lambda 핸들러가 S3 캐시 키 재료로 사용 — (symbol, as_of_date, stance,
    input_hash) 가 동일하면 LLM 호출을 생략하고 저장된 의견 재사용 → 운영
    레벨 100% 결정적 (docs §10 동등성 시나리오).
    """
    payload = ctx.model_dump_json().encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ---------- 메인 ----------

_LADDER: tuple[tuple[str, str], ...] = (
    ("primary", "primary"),
    ("primary", "primary_retry"),
    ("fallback", "fallback"),
)


def run_bullbear_agent(
    ctx: StockContext,
    stance: Literal["bull", "bear"],
    *,
    caller: AnthropicCaller,
    config: AgentConfig | None = None,
    pricing: dict[str, dict[str, float]] | None = None,
    purpose: str = PROMPT_PURPOSE,
) -> CallResult:
    """단일 종목·단일 stance LLM 호출. 재시도/폴백 사다리 적용.

    사다리:
      1. primary (Sonnet) 1차
      2. primary 재시도 — 1차가 네트워크/JSON/Pydantic 실패 시
      3. fallback (Haiku) — primary 두 번 모두 실패 시
    모든 호출 실패 시 BullBearAgentError 에 attempts 포함 raise.

    인자:
        ctx: 평탄화된 입력 컨텍스트.
        stance: "bull" 또는 "bear" — system 프롬프트와 메타 필드 양쪽에 사용.
        caller: Anthropic 호출 어댑터 (Lambda 핸들러는 SDK 래퍼, 테스트는 Fake).
        config: 모델·temperature·max_tokens 등. None 이면 기본값 (temperature=0).
        pricing: USD/1M 단가 매핑. None 이면 DEFAULT_PRICING.
        purpose: 로그의 purpose 필드 (CLAUDE.md 로깅 규칙).

    로그: 시도마다 1행 — stage/model/symbol/stance/tokens/cost/schema_valid.
    """
    cfg = config or AgentConfig()
    px = pricing or DEFAULT_PRICING
    system = _system_prompt(stance)
    user = _user_prompt(ctx, stance)

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
        except Exception as exc:  # noqa: BLE001 — caller 가 던질 수 있는 모든 예외 흡수
            attempts.append(
                CallAttempt(
                    model=model,
                    stage=stage,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                    succeeded=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            logger.warning(
                json.dumps(
                    {
                        "purpose": purpose,
                        "stage": stage,
                        "stance": stance,
                        "symbol": ctx.symbol,
                        "model": model,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            )
            continue

        # 2) 비용
        cost = _compute_cost(model, raw.input_tokens, raw.output_tokens, px)

        # 3) 파싱 + 검증
        try:
            opinion = _parse_opinion(
                raw.text,
                symbol=ctx.symbol,
                stance=stance,
                as_of_date=ctx.as_of_date,
                model=model,
                input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens,
                cost_usd=cost,
            )
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            attempts.append(
                CallAttempt(
                    model=model,
                    stage=stage,
                    input_tokens=raw.input_tokens,
                    output_tokens=raw.output_tokens,
                    cost_usd=cost,
                    succeeded=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            logger.warning(
                json.dumps(
                    {
                        "purpose": purpose,
                        "stage": stage,
                        "stance": stance,
                        "symbol": ctx.symbol,
                        "model": model,
                        "input_tokens": raw.input_tokens,
                        "output_tokens": raw.output_tokens,
                        "cost_usd": cost,
                        "schema_valid": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            )
            continue

        # 4) 성공 — 누적 attempts + 로그 + 반환
        attempts.append(
            CallAttempt(
                model=model,
                stage=stage,
                input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens,
                cost_usd=cost,
                succeeded=True,
            )
        )
        logger.info(
            json.dumps(
                {
                    "purpose": purpose,
                    "stage": stage,
                    "stance": stance,
                    "symbol": ctx.symbol,
                    "model": model,
                    "input_tokens": raw.input_tokens,
                    "output_tokens": raw.output_tokens,
                    "cost_usd": cost,
                    "schema_valid": True,
                }
            )
        )
        return CallResult(opinion=opinion, attempts=attempts)

    raise BullBearAgentError(
        f"all {len(_LADDER)} attempts failed for {ctx.symbol}/{stance}",
        attempts,
    )
