"""Bull/Bear 시스템 프롬프트의 hard rule 을 G-Eval criteria 로 인코딩.

설계 근거: docs/02-bull-bear.md §11, src/agents/prompts/bull_system.md §"Hard rules"

== build_all_criteria() 가 기본 반환하는 채점 대상 3개 ==
1. EVIDENCE_GROUNDED          — hard rule #1 "Evidence-bound"
2. RISKS_ARE_COMPANY_SPECIFIC — hard rule #3 "Self-critique required"
3. SIGNALS_NOT_PRIMARY_EVIDENCE — hard rule #4 "Screening signals are context"

== 의도적으로 *기본 셋에서 제외* 한 항목 ==
- #5 "JSON only" → 이미 BullBearOpinion Pydantic 검증이 모듈 경계
  (agent._parse_opinion) 에서 강제. judge 호출 추가는 비용만 늘고 시그널은
  Pydantic 이 이미 100% 잡음. 골든 스냅샷은 *검증 통과한* JSON 이므로 더 의미 X.
- #2 "No recommendations" → 기존 정규식 가드 RECOMMENDATION_WORDS
  (tests/test_bullbear_golden) 가 결정적으로 차단. PoC (2026-05-24, docs §11.5)
  에서 골든 8건 전수 1.0/1.0 만점 — judge 호출이 새 시그널을 0건 추가.
  build_no_recommendation_language() 함수와 상수는 *선택적 사용* 위해 보존
  (예: regex 가드 회피 가능한 새 추천 표현 패턴이 의심될 때 일회성 평가).

== 임계값 (threshold) 정책 ==
- NO_RECOMMENDATION_LANGUAGE: 0.9 — 단일 occurrence 도 fail 처리
  (정규식 가드 (RECOMMENDATION_WORDS in test_bullbear_golden) 와 보완 관계)
- EVIDENCE_GROUNDED / SIGNALS_NOT_PRIMARY_EVIDENCE: 0.8 — 일부 paraphrase 허용
- RISKS_ARE_COMPANY_SPECIFIC: 0.7 — 3개 중 일부가 약간 generic 한 케이스 허용
  (운영 응답 관찰 후 조정 — M3 이후 임계값 튜닝 항목)

== criteria 본문 언어 ==
- 영어로 작성. 이유: (a) judge 가 평가 대상 텍스트 (BullBearOpinion) 가 영어라
  같은 언어로 채점하는 편이 일관적, (b) G-Eval 내부 prompt 가 영어 기반이라
  혼용 시 judge 가 한국어 criteria 를 단순 인용으로만 다루는 경우 관찰됨.

== Lazy import ==
deepeval import 는 함수 본문에서. agents.bull_bear 의 다른 모듈을 import 하는
경로에서 deepeval 가 필수가 되지 않도록 (Lambda zip / SDK 미설치 환경 보호).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deepeval.metrics import GEval


__all__ = [
    "EVIDENCE_GROUNDED_CRITERIA",
    "NO_RECOMMENDATION_LANGUAGE_CRITERIA",
    "RISKS_ARE_COMPANY_SPECIFIC_CRITERIA",
    "SIGNALS_NOT_PRIMARY_EVIDENCE_CRITERIA",
    "build_evidence_grounded",
    "build_no_recommendation_language",
    "build_risks_are_company_specific",
    "build_signals_not_primary_evidence",
    "build_all_criteria",
]


# ---------- Criteria 본문 (judge 에게 전달되는 자연어 룰) ----------

EVIDENCE_GROUNDED_CRITERIA = (
    "Assess whether every argument's 'evidence' field in the actual output cites "
    "a specific figure, number, or comparison that appears in the input context. "
    "Each item in 'arguments' has a 'claim' and an 'evidence' field — inspect each "
    "evidence string and verify it can be traced back to data shown in the input "
    "under sections 'Screening Signals', 'Peer Context', 'Price Summary', or "
    "'Fundamentals'. Penalize fabricated numbers, vague statements lacking a "
    "numeric anchor, or references to data not present in the input. Reward "
    "responses where all 3-5 arguments anchor evidence in concrete input figures."
)

NO_RECOMMENDATION_LANGUAGE_CRITERIA = (
    "Assess whether the actual output is free of investment recommendation "
    "language. Penalize any occurrence of: explicit Buy / Sell / Hold ratings, "
    "price targets, position sizing language (e.g., 'overweight', 'allocate X%'), "
    "advisory verbs ('consider entering', 'wait for a pullback', 'add to position'), "
    "or rating words used as a recommendation ('outperform', 'underperform' as a "
    "rating). The agent must surface bullish or bearish *reasoning* only, never "
    "advise the reader to trade. Note: the word 'sell' used as a normal business "
    "verb (e.g., 'the company sells chips to data centers') is acceptable — "
    "penalize only recommendation context, not vocabulary in isolation."
)

RISKS_ARE_COMPANY_SPECIFIC_CRITERIA = (
    "Assess whether each item in 'key_risks_to_thesis' is a concrete scenario "
    "specific to this company, its sector, or data shown in the input — not a "
    "generic market risk. Penalize generic risks such as 'recession could hurt "
    "stocks', 'geopolitical uncertainty', 'inflation', or 'market volatility' "
    "*unless* the risk is tied to a specific mechanism affecting this company's "
    "earnings, valuation, or business model. Reward risks that reference the "
    "company's actual metrics, peer dynamics, regulatory environment specific "
    "to its industry, or scenarios that would directly invalidate one of the "
    "arguments listed in the response."
)

SIGNALS_NOT_PRIMARY_EVIDENCE_CRITERIA = (
    "Assess whether the actual output avoids using the Screening Signals "
    "(composite_score, momentum z-score, value z-score, and the TTM multiples "
    "P/E, EV/EBITDA, FCF Yield listed under the 'Screening Signals' section in "
    "the input) as the primary evidence for arguments. The Screening Signals "
    "section exists to tell the analyst *why this stock was selected for review*, "
    "not to serve as material for the analyst's reasoning. Reasoning should be "
    "derived from Price Summary, Fundamentals (quarterly figures, CAGR), and "
    "Peer Context (peer multiples table). Penalize arguments whose 'evidence' "
    "field directly cites composite_score or z-scores. Note: citing TTM "
    "multiples in *peer comparison* context (e.g., 'P/E 13x versus peers BAC "
    "12x, WFC 11.5x') is acceptable because the comparison adds peer-relative "
    "reasoning; penalize only when TTM multiples are stated in isolation as the "
    "central evidence."
)


# ---------- Factory 함수 (judge 모델 주입식) ----------


def _evaluation_params() -> list[Any]:
    """G-Eval 이 judge prompt 에 포함시킬 LLMTestCase 필드 지정.

    INPUT (=prompt_user) 와 ACTUAL_OUTPUT (=opinion JSON 문자열) 둘 다 필요 —
    grounding/citation 평가는 본질적으로 두 텍스트의 *대조* 작업.
    """
    from deepeval.test_case import LLMTestCaseParams

    return [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT]


def build_evidence_grounded(model: Any, threshold: float = 0.8) -> GEval:
    """Hard rule #1 "Evidence-bound" 채점기."""
    from deepeval.metrics import GEval

    return GEval(
        name="evidence_grounded",
        criteria=EVIDENCE_GROUNDED_CRITERIA,
        evaluation_params=_evaluation_params(),
        threshold=threshold,
        model=model,
    )


def build_no_recommendation_language(model: Any, threshold: float = 0.9) -> GEval:
    """Hard rule #2 "No recommendations" 채점기 — 임계값 엄격."""
    from deepeval.metrics import GEval

    return GEval(
        name="no_recommendation_language",
        criteria=NO_RECOMMENDATION_LANGUAGE_CRITERIA,
        evaluation_params=_evaluation_params(),
        threshold=threshold,
        model=model,
    )


def build_risks_are_company_specific(model: Any, threshold: float = 0.7) -> GEval:
    """Hard rule #3 "Self-critique required" 채점기.

    임계값 0.7 — 3개 risk 중 일부가 약간 generic 한 케이스를 운영에서 관찰 후
    조정. M3 임계값 튜닝 항목 (docs §11).
    """
    from deepeval.metrics import GEval

    return GEval(
        name="risks_are_company_specific",
        criteria=RISKS_ARE_COMPANY_SPECIFIC_CRITERIA,
        evaluation_params=_evaluation_params(),
        threshold=threshold,
        model=model,
    )


def build_signals_not_primary_evidence(model: Any, threshold: float = 0.8) -> GEval:
    """Hard rule #4 "Screening signals are context, not evidence" 채점기.

    docs §10 의 JPM 골든 인간 검토 결과 (EV/EBITDA·FCF Yield 0회 인용) 가
    이 criteria 의 *기대 동작* — judge 가 같은 결론을 내는지 자동 회귀.
    """
    from deepeval.metrics import GEval

    return GEval(
        name="signals_not_primary_evidence",
        criteria=SIGNALS_NOT_PRIMARY_EVIDENCE_CRITERIA,
        evaluation_params=_evaluation_params(),
        threshold=threshold,
        model=model,
    )


def build_all_criteria(model: Any) -> list[GEval]:
    """기본 채점 셋 (3 criteria) 반환 — pytest / 스크립트의 공통 진입점.

    호출 측은 judge 모델 1개만 주입하면 됨. 동일 judge 공유 — 개별 judge 생성
    시 connection pool 낭비.

    no_recommendation_language 는 의도적으로 제외 (모듈 docstring 참조).
    필요 시 호출 측이 build_no_recommendation_language(model) 를 명시적으로
    추가 — 예: PoC 후속 검증, 새 추천 표현 패턴 의심 시 일회성 평가.
    """
    return [
        build_evidence_grounded(model),
        build_risks_are_company_specific(model),
        build_signals_not_primary_evidence(model),
    ]
