"""시나리오 프롬프트 파일의 정적 일관성 가드.

설계 근거: docs/03-scenario.md §3.2, §3.3

LLM 호출 없이 프롬프트 파일 자체의 drift 를 잡는다 — 필수 룰의 존재, user
템플릿 placeholder 셋, 그리고 시스템 프롬프트의 metric enum 이 실제 스키마
(InvalidationTrigger) 와 일치하는지 (prompt↔schema drift 가드).

의미 동등성(LLM 의 *실제* 답변이 룰을 따르는가) 은 골든 케이스(§10.5/§11 #8)
에서 검증 — 본 테스트는 형태 층위만.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from agents.scenario.schemas import InvalidationTrigger

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "agents" / "prompts"

SYSTEM_REQUIRED = (
    "No price estimation",
    "JSON only",
    "sum to 1.0",
    "invalidation_trigger",
    "qualitative",
    "negate",                 # P2-H — 트리거가 시나리오 부정
    "metric ↔ threshold_unit",  # P1-E — unit 정합 규칙
    "net_debt_yoy",
    "Financials or Utilities",  # v0.4 — net_debt_yoy 자제
)

USER_PLACEHOLDERS_EXPECTED = {"context", "symbol", "as_of_date"}


def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


# ---------- 파일 존재 ----------


def test_prompt_files_exist() -> None:
    assert (PROMPTS_DIR / "scenario_system.md").is_file()
    assert (PROMPTS_DIR / "scenario_user.md").is_file()


# ---------- 시스템 프롬프트 필수 룰 ----------


def test_system_required_keywords() -> None:
    system = _read("scenario_system.md")
    for kw in SYSTEM_REQUIRED:
        assert kw in system, f"시스템 프롬프트에 '{kw}' 누락"


def test_system_mentions_all_schema_metrics() -> None:
    """prompt↔schema drift 가드 — 스키마의 10개 metric 이 프롬프트에 모두 명시."""
    system = _read("scenario_system.md")
    metrics = get_args(InvalidationTrigger.model_fields["metric"].annotation)
    assert len(metrics) == 10
    for metric in metrics:
        assert metric in system, f"metric '{metric}' 가 시스템 프롬프트에 없음"


def test_system_lists_three_labels() -> None:
    system = _read("scenario_system.md")
    for label in ("bull", "base", "bear"):
        assert label in system


# ---------- user 템플릿 placeholder ----------


def test_user_placeholders_exact() -> None:
    user = _read("scenario_user.md")
    found = set(re.findall(r"\{(\w+)\}", user))
    assert found == USER_PLACEHOLDERS_EXPECTED, (
        f"placeholder 불일치: {found} != {USER_PLACEHOLDERS_EXPECTED}"
    )


def test_user_no_stance_placeholder() -> None:
    # Bull/Bear 와 달리 시나리오는 single-call — stance placeholder 없음
    assert "{stance}" not in _read("scenario_user.md")
