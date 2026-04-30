"""골든 케이스 회귀 검증 — 저장된 스냅샷이 schema·정책을 만족하는지.

설계 근거: docs/02-bull-bear.md §8.3, §9 #6, §10

이 테스트는 LLM 호출을 *새로 하지 않는다* — scripts/run_bullbear_golden.py
가 저장한 JSON 만 검증. 실제 호출은 그 스크립트가 별도로 트리거.

마커: pytest -m golden
- 통상 CI 는 -m "not golden" 으로 실행 (스냅샷이 없거나 변동될 수 있어).
- 골든 스냅샷 갱신 시: scripts 실행 → 본 테스트로 회귀 확인 → 인간 검토 →
  커밋.

스냅샷이 없으면 parametrize 가 0 items 를 생성 — pytest 는 deselected 처럼
처리되어 false-positive pass 가 안 생긴다 (의도).

검증 항목 (LLM 응답이 *실제로* 시스템 프롬프트의 룰을 따랐는지):
1. BullBearOpinion 스키마 통과
2. 추천 어휘 미출현 (Buy/Sell/Hold/Target 등)
3. evidence 가 비어 있지 않음 (시스템 프롬프트 hard rule #1)
4. 파일명의 stance 와 opinion.stance 일치 (메타 주입 회귀)
5. 사다리에서 최소 1회 succeeded
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agents.bull_bear.schemas import BullBearOpinion

GOLDEN_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "golden" / "bullbear"

# 추천 어휘 — Bull/Bear 모두 절대 출현하면 안 됨 (시스템 프롬프트 hard rule #2).
#
# 정밀화 (NVDA_bull 골든 첫 실행에서 false-positive 발견 후): "buy/sell/hold"
# 단독은 일반 동사로도 흔히 쓰인다 — 예: "NVDA's ability to sell chips",
# "buyers in the market", "company has to hold reserves". 추천 *컨텍스트*
# (Wall Street rating 형식, target price, 명시적 권고문구) 만 매치하도록 좁힘.
#
# 매치되는 패턴:
# - target price / price target
# - outperform / underperform / overweight / underweight / market perform
# - strong buy / strong sell
# - rate(d|s) (a|as) (buy|sell|hold)         예: "we rate it a buy"
# - rating: (buy|sell|hold) 또는 비슷한 형태
# - recommend(s|ed|ation) (to)? (buy|sell|hold)
RECOMMENDATION_WORDS = re.compile(
    r"\b(?:"
    r"target\s+price|price\s+target|"
    r"strong\s+(?:buy|sell)|"
    r"outperform|underperform|"
    r"overweight|underweight|"
    r"market\s+perform|"
    r"recommend(?:s|ed|ation)?\s+(?:to\s+)?(?:buy|sell|hold)|"
    r"rate(?:d|s)?\s+(?:it\s+)?(?:a\s+|as\s+)?(?:buy|sell|hold)|"
    r"rating[:\s]+(?:buy|sell|hold)"
    r")\b",
    re.IGNORECASE,
)


def _discover_snapshots() -> list[Path]:
    if not GOLDEN_DIR.exists():
        return []
    return sorted(GOLDEN_DIR.glob("*.json"))


SNAPSHOTS = _discover_snapshots()


pytestmark = [
    pytest.mark.golden,
    # 스냅샷이 없으면 모듈 자체를 skip — parametrize 가 0 items 가 되어
    # 사용자가 "테스트 안 돌았는데 통과처럼 보임" 을 겪지 않도록.
    pytest.mark.skipif(
        not SNAPSHOTS,
        reason=(
            "골든 스냅샷 없음. scripts/run_bullbear_golden.py 실행해 "
            f"{GOLDEN_DIR.relative_to(GOLDEN_DIR.parent.parent.parent)} 에 생성하세요."
        ),
    ),
]


@pytest.fixture(params=SNAPSHOTS, ids=lambda p: p.name)
def snapshot(request: pytest.FixtureRequest) -> tuple[Path, dict]:
    path: Path = request.param
    payload = json.loads(path.read_text(encoding="utf-8"))
    return path, payload


def test_snapshot_has_required_keys(snapshot: tuple[Path, dict]) -> None:
    path, payload = snapshot
    missing = {"opinion", "attempts", "prompt_user", "input_hash"} - set(payload)
    assert not missing, f"{path.name}: 누락 키 {missing}"


def test_snapshot_passes_schema(snapshot: tuple[Path, dict]) -> None:
    path, payload = snapshot
    try:
        BullBearOpinion.model_validate(payload["opinion"])
    except Exception as exc:
        pytest.fail(f"{path.name}: schema 위반 — {exc}")


def test_snapshot_no_recommendation_words(snapshot: tuple[Path, dict]) -> None:
    """시스템 프롬프트 hard rule #2 — 추천 어휘 금지."""
    path, payload = snapshot
    opinion = BullBearOpinion.model_validate(payload["opinion"])
    haystacks: list[tuple[str, str]] = [("summary", opinion.summary)]
    for i, arg in enumerate(opinion.arguments):
        haystacks.append((f"arguments[{i}].claim", arg.claim))
        haystacks.append((f"arguments[{i}].evidence", arg.evidence))
    for i, risk in enumerate(opinion.key_risks_to_thesis):
        haystacks.append((f"key_risks_to_thesis[{i}]", risk))

    for label, text in haystacks:
        m = RECOMMENDATION_WORDS.search(text)
        assert m is None, (
            f"{path.name}/{label}: 추천 어휘 '{m.group(0)}' 출현 — "
            f"시스템 프롬프트 hard rule #2 위반. 원문: {text!r}"
        )


def test_snapshot_evidence_non_empty(snapshot: tuple[Path, dict]) -> None:
    """시스템 프롬프트 hard rule #1 — evidence-bound."""
    path, payload = snapshot
    opinion = BullBearOpinion.model_validate(payload["opinion"])
    for i, arg in enumerate(opinion.arguments):
        assert arg.evidence.strip(), (
            f"{path.name}/arguments[{i}].evidence 가 공백/빈 문자열"
        )


def test_snapshot_stance_matches_filename(snapshot: tuple[Path, dict]) -> None:
    """파일명 'AAPL_bull.json' 의 stance 가 opinion.stance 와 일치 — 메타 주입
    회귀."""
    path, payload = snapshot
    stem_parts = path.stem.rsplit("_", 1)
    if len(stem_parts) != 2:
        pytest.fail(f"{path.name}: 파일명 규칙 위반 ({{symbol}}_{{stance}}.json)")
    expected_symbol, expected_stance = stem_parts
    opinion = BullBearOpinion.model_validate(payload["opinion"])
    assert opinion.symbol == expected_symbol, (
        f"{path.name}: symbol 불일치 ({opinion.symbol} != {expected_symbol})"
    )
    assert opinion.stance == expected_stance, (
        f"{path.name}: stance 불일치 ({opinion.stance} != {expected_stance})"
    )


def test_snapshot_at_least_one_attempt_succeeded(snapshot: tuple[Path, dict]) -> None:
    """최종 성공한 attempt 가 있어야 스냅샷 저장됨 — 사다리 검증."""
    path, payload = snapshot
    attempts = payload.get("attempts", [])
    assert any(a.get("succeeded") for a in attempts), (
        f"{path.name}: 성공한 attempt 없음"
    )
