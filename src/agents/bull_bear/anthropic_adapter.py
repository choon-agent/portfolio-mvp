"""Anthropic SDK 어댑터 — agent.py 의 AnthropicCaller Protocol 구현.

설계 근거: docs/02-bull-bear.md §3.1, §9 #5/#6/#7

이 모듈은 실제 Anthropic API 호출이 일어나는 *유일한* 지점. 단위 테스트는
FakeAnthropicClient (test_bullbear_agent) 로 충분하므로 본 어댑터에 대한
단위 테스트는 두지 않는다 — 실제 동작 검증은 골든 케이스 (scripts/
run_bullbear_golden.py) 가 통합 테스트 역할.

SDK 의존:
- anthropic >=0.40 (requirements.txt). lazy import — SDK 미설치 환경에서
  agents.bull_bear 의 다른 모듈(schemas, mappers, agent, context_builder)
  은 영향 없음.

재시도 정책:
- max_retries=0 — agent.py 의 사다리(primary → primary_retry → fallback) 가
  재시도 책임. SDK 자체 재시도를 켜면 사다리 의도와 충돌(검증 실패 시 사다리
  통과 못 하고 SDK 안에서 같은 모델로만 재시도하는 케이스 등).
"""
from __future__ import annotations

import logging

from agents.bull_bear.agent import RawCompletion

logger = logging.getLogger(__name__)


class AnthropicSDKCaller:
    """anthropic.Anthropic.messages.create → RawCompletion 정규화."""

    def __init__(self, *, api_key: str | None = None) -> None:
        try:
            import anthropic  # noqa: F401 — lazy import, 미설치 시 명확한 에러
        except ImportError as exc:
            raise RuntimeError(
                "anthropic SDK 미설치. requirements.txt 에 명시되어 있으니 "
                "`pip install -r requirements.txt` 실행 필요."
            ) from exc

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=0)

    def call(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
    ) -> RawCompletion:
        msg = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )

        text_parts: list[str] = []
        for block in msg.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(block.text)

        if not text_parts:
            raise ValueError(
                f"Anthropic 응답에 text block 없음 (stop_reason={msg.stop_reason})"
            )

        return RawCompletion(
            text="".join(text_parts),
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
        )
