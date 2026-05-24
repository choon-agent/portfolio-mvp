"""Bull/Bear 응답 품질 평가 — DeepEval G-Eval 회귀.

설계 근거: docs/02-bull-bear.md §11, src/agents/bull_bear/evaluation/

이 테스트는 *judge LLM 호출이 실제로 발생*한다 (Sonnet 4.6 × 3 criteria × 8
snapshot ≈ $0.37/회 — docs §11.5 baseline). 통상 CI 에서는 `-m "not deepeval"`
로 제외하고, 프롬프트 수정 시 / 주간 회귀 시점에 수동 트리거.

테스트 구조:
- 기존 골든 스냅샷 (tests/golden/bullbear/*.json) 을 *입력 소스* 로 재사용.
  test_bullbear_golden 이 schema / 추천 어휘 / stance 회귀를 가드한다면,
  본 테스트는 *내용 품질* (evidence grounding, signals usage, risk specificity)
  을 LLM-as-judge 로 가드.
- AnthropicJudge 1개를 module-scoped fixture 로 공유 — connection pool 재사용
  + cost 누적 가시화.
- snapshot × criteria 평면 parametrize — 어느 (snapshot, criteria) pair 가
  실패했는지 pytest output 에서 즉시 식별 가능.

전제 조건:
- deepeval 설치 (requirements-dev.txt). 미설치 시 모듈 단위 skip.
- ANTHROPIC_API_KEY 환경변수. 미설정 시 모듈 단위 skip.
- 골든 스냅샷 존재. 없으면 모듈 단위 skip (test_bullbear_golden 과 동일 정책).

실행:
  PYTHONPATH=src ANTHROPIC_API_KEY=sk-... .venv/bin/pytest src/tests/test_bullbear_deepeval.py -m deepeval -v
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

# deepeval 미설치 시 모듈 단위 skip — 본 테스트 외 다른 단위 테스트 영향 X.
pytest.importorskip("deepeval", reason="deepeval 미설치 — requirements-dev.txt 확인.")

from agents.bull_bear.evaluation.adapters import (  # noqa: E402
    GOLDEN_DIR,
    AnthropicJudge,
    discover_snapshots,
    load_snapshot,
    snapshot_to_test_case,
)
from agents.bull_bear.evaluation.criteria import (  # noqa: E402
    build_evidence_grounded,
    build_risks_are_company_specific,
    build_signals_not_primary_evidence,
)


SNAPSHOTS = discover_snapshots()


pytestmark = [
    pytest.mark.deepeval,
    pytest.mark.skipif(
        not SNAPSHOTS,
        reason=(
            f"골든 스냅샷 없음 ({GOLDEN_DIR}). scripts/run_bullbear_golden.py "
            "실행해 생성 후 재실행."
        ),
    ),
    pytest.mark.skipif(
        os.environ.get("ANTHROPIC_API_KEY") is None,
        reason="ANTHROPIC_API_KEY 환경변수 필요 (judge 호출).",
    ),
]


# ---------- Fixtures ----------


@pytest.fixture(scope="module")
def judge() -> Any:
    """Sonnet 4.6 judge — 본 모듈의 모든 테스트가 공유.

    module scope: snapshot × criteria 매 조합마다 client 재생성 비용 회피.
    """
    return AnthropicJudge()


# Parametrize 배열 — (snapshot 경로, criteria 빌더, criteria 이름).
# 평면 펼침으로 어느 (snapshot, criteria) 가 실패했는지 pytest output 에서 즉시 식별.
#
# no_recommendation_language 는 의도적으로 제외 — PoC 2026-05-24 (docs §11.5)
# 에서 골든 8건 전수 1.0/1.0, 기존 정규식 가드 (test_bullbear_golden 의
# RECOMMENDATION_WORDS) 가 이미 결정적으로 보장. 필요 시 build_no_recommendation
# _language 를 명시 import 해 추가 가능.
_CRITERIA_BUILDERS: list[tuple[str, Any]] = [
    ("evidence_grounded", build_evidence_grounded),
    ("risks_are_company_specific", build_risks_are_company_specific),
    ("signals_not_primary_evidence", build_signals_not_primary_evidence),
]


def _id_for(snapshot_path: Path, criteria_name: str) -> str:
    return f"{snapshot_path.stem}-{criteria_name}"


_PARAMS = [
    pytest.param(path, name, builder, id=_id_for(path, name))
    for path in SNAPSHOTS
    for name, builder in _CRITERIA_BUILDERS
]


# ---------- 테스트 ----------


@pytest.mark.parametrize("snapshot_path, criteria_name, criteria_builder", _PARAMS)
def test_geval_criterion_passes(
    snapshot_path: Path,
    criteria_name: str,
    criteria_builder: Any,
    judge: Any,
) -> None:
    """단일 (snapshot, criteria) — G-Eval 통과 검증.

    실패 시 DeepEval 의 AssertionError 가 criteria 의 score 와 reasoning 을
    포함해 raise → 어떤 룰을 어떻게 위반했는지 pytest output 에서 즉시 파악.
    """
    from deepeval import assert_test

    snapshot = load_snapshot(snapshot_path)
    test_case = snapshot_to_test_case(snapshot, name=snapshot_path.stem)
    metric = criteria_builder(judge)

    assert_test(test_case, [metric])
