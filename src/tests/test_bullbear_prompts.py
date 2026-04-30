"""Bull/Bear 프롬프트 파일의 정적 일관성 가드.

설계 근거: docs/02-bull-bear.md §3.2 (시스템 프롬프트 핵심), §10
(동등성 정책의 형태 일관성 층위)

이 테스트들은 LLM 호출 없이 프롬프트 파일 자체의 drift 를 잡는다 — bull
↔ bear 미러 구조, 필수 룰의 양쪽 존재, user 템플릿의 placeholder 셋.

의미 동등성(LLM 의 *실제* 답변이 룰을 따르는가) 은 별도 골든 케이스 (§9 #6)
에서 검증한다 — 본 테스트는 형태 층위만 책임.
"""
from __future__ import annotations

import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "agents" / "prompts"

REQUIRED_KEYWORDS = (
    "evidence",
    "JSON only",
    "key_risks_to_thesis",
    "Buy / Hold / Sell",
    "Financials",
    "confidence",
    "200 characters",
)

USER_PLACEHOLDERS_EXPECTED = {"context", "stance", "symbol", "as_of_date"}


# ---------- 공통 ----------


def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _section_headers(markdown: str) -> list[str]:
    """## 헤더만 추출. # (제목) 은 미러 검증에서 제외 — bull/bear 가 다른 게 정상."""
    return re.findall(r"^##\s+(.+)$", markdown, re.MULTILINE)


# ---------- 파일 존재 ----------


def test_all_three_prompt_files_exist():
    for name in ("bull_system.md", "bear_system.md", "bullbear_user.md"):
        assert (PROMPTS_DIR / name).exists(), f"{name} 누락"


# ---------- bull/bear system 미러 구조 ----------


def test_bull_bear_systems_share_same_section_headers():
    """drift 방지 — 한쪽에만 룰을 추가해 두는 실수 차단."""
    bull = _section_headers(_read("bull_system.md"))
    bear = _section_headers(_read("bear_system.md"))
    assert sorted(bull) == sorted(bear), (
        f"bull/bear system prompt 의 섹션 헤더 불일치:\n"
        f"  bull: {sorted(bull)}\n"
        f"  bear: {sorted(bear)}"
    )


def test_bull_bear_systems_have_similar_length():
    """미러 구조면 길이도 비슷해야 함 — 한쪽이 ±50% 차이면 drift 의심."""
    bull = len(_read("bull_system.md"))
    bear = len(_read("bear_system.md"))
    ratio = max(bull, bear) / min(bull, bear)
    assert ratio < 1.5, f"bull/bear system prompt 길이 비율 {ratio:.2f} — drift 의심"


# ---------- 양쪽에 필수 룰 존재 ----------


def test_bull_system_contains_required_keywords():
    text = _read("bull_system.md")
    missing = [kw for kw in REQUIRED_KEYWORDS if kw not in text]
    assert not missing, f"bull_system.md 누락 키워드: {missing}"


def test_bear_system_contains_required_keywords():
    text = _read("bear_system.md")
    missing = [kw for kw in REQUIRED_KEYWORDS if kw not in text]
    assert not missing, f"bear_system.md 누락 키워드: {missing}"


def test_systems_promise_argument_count_3_to_5():
    """schema 의 arguments min/max(3/5) 와 프롬프트 약속 정합 — test_bullbear_schemas
    와 짝을 이루는 가드 (한쪽만 바뀌면 silent 불일치)."""
    for name in ("bull_system.md", "bear_system.md"):
        text = _read(name)
        # `3-5` 또는 `3–5` (en-dash)
        assert re.search(r"3\s*[-–]\s*5", text), f"{name}: arguments 3-5 약속 누락"


def test_systems_promise_risks_count_1_to_3():
    """schema 의 key_risks_to_thesis min/max(1/3) 와 프롬프트 약속 정합."""
    for name in ("bull_system.md", "bear_system.md"):
        text = _read(name)
        assert re.search(r"1\s*[-–]\s*3", text), f"{name}: key_risks 1-3 약속 누락"


# ---------- user 템플릿 placeholder ----------


def test_user_prompt_has_exactly_expected_placeholders():
    """{context}, {stance}, {symbol}, {as_of_date} — agent.py 의 _user_prompt 와 정합."""
    text = _read("bullbear_user.md")
    found = set(re.findall(r"\{(\w+)\}", text))
    assert found == USER_PLACEHOLDERS_EXPECTED, (
        f"placeholder 불일치 — found: {sorted(found)}, "
        f"expected: {sorted(USER_PLACEHOLDERS_EXPECTED)}"
    )


def test_user_prompt_has_no_unbalanced_braces():
    """짝 없는 단일 중괄호가 placeholder 외에 없는지 — replace 안전성."""
    text = _read("bullbear_user.md")
    # placeholder 를 모두 치우고 남은 텍스트에 { 또는 } 가 있으면 안 됨
    stripped = re.sub(r"\{(?:" + "|".join(USER_PLACEHOLDERS_EXPECTED) + r")\}", "", text)
    assert "{" not in stripped and "}" not in stripped, (
        "bullbear_user.md 에 정의된 placeholder 외 중괄호 존재 — replace 시 노이즈"
    )


# ---------- 의미 거울상 (간단 체크) ----------


def test_bull_system_self_identifies_as_bull():
    text = _read("bull_system.md").lower()
    assert "bull" in text
    # bear 측 자기소개 단어가 들어가면 안 됨 (단, 반증 시나리오 등에 등장 가능 →
    # 첫 문단만 검증)
    first_paragraph = text.split("\n\n", 1)[0]
    assert "long-side" in first_paragraph or "bull" in first_paragraph


def test_bear_system_self_identifies_as_bear():
    text = _read("bear_system.md").lower()
    assert "bear" in text
    first_paragraph = text.split("\n\n", 1)[0]
    assert "short-side" in first_paragraph or "bear" in first_paragraph or "negative" in first_paragraph
