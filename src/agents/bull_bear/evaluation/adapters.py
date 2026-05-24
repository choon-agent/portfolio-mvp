"""DeepEval ↔ Bull/Bear 자산 사이의 어댑터.

설계 근거: docs/02-bull-bear.md §8.3 (골든 스냅샷 형식), §11

본 모듈의 책임:
1. AnthropicJudge — DeepEval 의 DeepEvalBaseLLM 구현 (judge = Sonnet 4.6).
   anthropic SDK 직접 호출. agents.bull_bear.anthropic_adapter 와는 *별도* —
   그쪽은 BullBear 자체 호출, 본 어댑터는 judge 전용 (max_retries / 에러 처리
   요구가 다르고, judge 출력은 구조화 schema 가 붙는 경우가 있어 generate 시그
   니처가 다름).
2. 골든 스냅샷 로더 — tests/golden/bullbear/*.json (run_bullbear_golden.py 가
   저장한 형식) → DeepEval LLMTestCase.
   * INPUT          = snapshot["prompt_user"]  ← LLM 이 본 정확한 user 프롬프트
   * ACTUAL_OUTPUT  = snapshot["opinion"] JSON 직렬화 문자열
   prompt_user 를 *그대로* 쓰는 이유: 평가가 "LLM 이 본 것과 동일한 context"
   위에서 채점돼야 grounding 평가가 의미 있음. context_builder 를 재호출해
   재조립하면 fixture 변경 시 evaluation 결과가 의도치 않게 흔들림.

Lazy import 정책: deepeval / anthropic 은 모두 함수 본문에서 import. 본 모듈
import 자체는 가벼움 — Lambda zip 에 동봉돼도 cold start 무영향.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deepeval.models import DeepEvalBaseLLM
    from deepeval.test_case import LLMTestCase


__all__ = [
    "AnthropicJudge",
    "GOLDEN_DIR",
    "DEFAULT_JUDGE_MODEL",
    "load_snapshot",
    "snapshot_to_test_case",
    "discover_snapshots",
]


# tests/golden/bullbear/ 의 절대 경로 — repo root 기준. 본 모듈은 src/agents/
# bull_bear/evaluation/ 에 있으므로 4단계 위가 repo root.
GOLDEN_DIR = (Path(__file__).resolve().parents[4] / "tests" / "golden" / "bullbear")

DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"

# Judge 응답 상한. BullBear 본 호출(2048)보다 작아도 충분 — judge 출력은 score
# + reasoning 한 단락 정도. 너무 작게 잡으면 reasoning 잘림 → DeepEval 파싱 실패.
_JUDGE_MAX_TOKENS = 2048


# ---------- Judge LLM 어댑터 ----------


def _import_deepeval_base() -> type[DeepEvalBaseLLM]:
    """DeepEvalBaseLLM lazy import — 본 모듈 import 가 deepeval 미설치에 깨지지 않게."""
    try:
        from deepeval.models import DeepEvalBaseLLM
    except ImportError as exc:
        raise RuntimeError(
            "deepeval 미설치. requirements-dev.txt 의 deepeval 항목 확인 후 "
            "`pip install -r requirements-dev.txt` 실행 필요."
        ) from exc
    return DeepEvalBaseLLM


def _make_anthropic_judge_class() -> type:
    """AnthropicJudge 클래스를 lazy 생성 — DeepEvalBaseLLM 상속이 필요한데,
    base class 자체가 lazy import 라 클래스 정의도 함수 안에서 만든다.
    """
    Base = _import_deepeval_base()

    class _AnthropicJudge(Base):  # type: ignore[misc, valid-type]
        """DeepEval judge — Anthropic SDK 로 Sonnet 4.6 호출.

        DeepEval 가 GEval 채점 시 judge.generate(prompt) 또는 generate(prompt,
        schema=PydanticModel) 형태로 호출한다. schema 가 주어지면 응답 본문
        JSON 을 Pydantic 으로 검증해 반환 — DeepEval 의 구조화 출력 경로.
        """

        def __init__(self, model: str = DEFAULT_JUDGE_MODEL, *, api_key: str | None = None) -> None:
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError(
                    "anthropic SDK 미설치 — requirements.txt 의 anthropic 확인."
                ) from exc

            self._model = model
            self._client = anthropic.Anthropic(api_key=api_key, max_retries=0)

        # DeepEvalBaseLLM 인터페이스 ----------

        def load_model(self) -> Any:
            """DeepEval 가 호출 — 우리는 client 를 직접 관리하므로 self 반환."""
            return self

        def get_model_name(self) -> str:
            return self._model

        def generate(self, prompt: str, schema: Any = None) -> Any:
            """동기 호출. schema 주어지면 Pydantic 검증 후 모델 인스턴스 반환."""
            response = self._client.messages.create(
                model=self._model,
                max_tokens=_JUDGE_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )

            text_parts: list[str] = []
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    text_parts.append(block.text)
            text = "".join(text_parts)

            if schema is None:
                return text

            # DeepEval 의 일부 metric 은 schema 를 강제 — 응답 본문에서 JSON
            # 추출 후 model_validate_json. 단순한 best-effort: 첫 번째 {...} 블록.
            stripped = _strip_json_fence(text)
            try:
                return schema.model_validate_json(stripped)
            except Exception:
                # 마지막 시도: 본문 전체에서 첫 { ~ 마지막 } 추출
                start = stripped.find("{")
                end = stripped.rfind("}")
                if start >= 0 and end > start:
                    return schema.model_validate_json(stripped[start : end + 1])
                raise

        async def a_generate(self, prompt: str, schema: Any = None) -> Any:
            """비동기 wrapper — PoC 는 sync 만 써도 충분. DeepEval 의 일부 코드
            경로가 a_generate 를 요구해 동기 호출로 위임.
            """
            return self.generate(prompt, schema)

    return _AnthropicJudge


def AnthropicJudge(model: str = DEFAULT_JUDGE_MODEL, *, api_key: str | None = None) -> Any:
    """공개 factory — 호출 측이 평범한 함수처럼 사용.

    클래스 자체가 lazy 라 직접 노출하지 않고 factory 로 감싼다. 호출 측은
    `judge = AnthropicJudge()` 한 줄로 끝.
    """
    cls = _make_anthropic_judge_class()
    resolved_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
    if resolved_key is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY 환경변수 또는 api_key 인자 필요 (judge 호출용)."
        )
    return cls(model=model, api_key=resolved_key)


# ---------- 골든 스냅샷 로더 ----------


def _strip_json_fence(text: str) -> str:
    """```json ... ``` 펜스 제거. agent._strip_json_fence 와 의도 동일하나 단방향
    의존을 피해 자체 구현 (evaluation 패키지가 agent 핵심 모듈을 import 하지 않게)."""
    import re

    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def load_snapshot(path: Path) -> dict:
    """골든 JSON 로드 + 최소 형태 검증.

    스냅샷 구조 (run_bullbear_golden.py:_save_snapshot 참조):
      {"opinion": {...}, "attempts": [...], "prompt_user": "...", "input_hash": "..."}

    엄격한 schema 검증은 test_bullbear_golden 이 이미 수행 (Pydantic). 여기선
    deepeval 평가에 *반드시 필요한* 두 키 (opinion, prompt_user) 만 확인.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    for key in ("opinion", "prompt_user"):
        if key not in raw:
            raise ValueError(f"{path.name}: 필수 키 '{key}' 누락 — 스냅샷 형식 확인.")
    return raw


def snapshot_to_test_case(snapshot: dict, *, name: str | None = None) -> LLMTestCase:
    """골든 스냅샷 dict → DeepEval LLMTestCase.

    actual_output 은 opinion dict 를 JSON 문자열로 직렬화. judge 가 구조화 응답
    내용 (arguments[i].evidence, key_risks_to_thesis 등) 을 정확히 인용할 수
    있도록 키 보존 — 마크다운 변환은 정보 손실 위험.

    name 인자: pytest parametrize id 와 일치시키려면 호출 측에서 명시. None 이면
    DeepEval 가 자동 생성.
    """
    from deepeval.test_case import LLMTestCase

    return LLMTestCase(
        name=name,
        input=snapshot["prompt_user"],
        actual_output=json.dumps(snapshot["opinion"], ensure_ascii=False, indent=2),
    )


def discover_snapshots(directory: Path = GOLDEN_DIR) -> list[Path]:
    """tests/golden/bullbear/ 의 *.json 정렬 반환 (.gitkeep 등 비-JSON 제외).

    정렬 — pytest parametrize 결과의 결정성 보장.
    """
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())
