"""옵션 B baseline — Bull/Bear confidence 코드 점수화 (04 §6, 03 §1.4.2 #3).

옵션 C(LLM 시나리오 확률) 와의 portfolio outcome 비교를 위해, 같은 가격
산식에 *확률만* Bull/Bear confidence 가중치로 교체한 ExpectedReturn 을
산출한다. LLM 호출 0 — 기존 opinion 재사용.

확률 매핑 (04 §6 확정):
  score = Σ CONFIDENCE_SCORE[argument.confidence]   (low=1 / medium=2 / high=3)
  p_base = 0.34 (고정 중립 질량)
  p_bull = score_bull / (score_bull + score_bear) × (1 - p_base)
  p_bear = 1 - p_base - p_bull

순수 함수 — pypfopt/numpy 불요 (agents 스키마 + pricing 재사용만).
"""
from __future__ import annotations

from agents.bull_bear.schemas import BullBearOpinion
from agents.scenario.pricing import compute_expected_return
from agents.scenario.pricing_config import ScenarioPricingConfig
from agents.scenario.schemas import ExpectedReturn, ScenarioContext, ScenarioOpinion

__all__ = ["option_b_probabilities", "option_b_expected_return"]

CONFIDENCE_SCORE = {"low": 1, "medium": 2, "high": 3}
P_BASE = 0.34


def option_b_probabilities(
    bull: BullBearOpinion, bear: BullBearOpinion
) -> dict[str, float]:
    """confidence 점수 비율 → 3-class 확률. 합 = 1.0 보장.

    arguments 는 스키마상 3~5개·confidence 필수 → score ≥ 3 (0 나눗셈 불가).
    """
    s_bull = sum(CONFIDENCE_SCORE[a.confidence] for a in bull.arguments)
    s_bear = sum(CONFIDENCE_SCORE[a.confidence] for a in bear.arguments)
    p_bull = s_bull / (s_bull + s_bear) * (1 - P_BASE)
    return {"bull": p_bull, "base": P_BASE, "bear": 1 - P_BASE - p_bull}


def option_b_expected_return(
    opinion: ScenarioOpinion,
    ctx: ScenarioContext,
    cfg: ScenarioPricingConfig,
) -> ExpectedReturn:
    """옵션 B ExpectedReturn — 확률만 교체, 가격 산식·config 동일.

    ctx 의 bull/bear opinion 에서 확률을 산출해 ScenarioOpinion 사본에 주입 후
    기존 compute_expected_return 재사용 (확률 cap 등 §4.1 경로 그대로).
    """
    probs = option_b_probabilities(ctx.bull_opinion, ctx.bear_opinion)
    replaced = opinion.model_copy(
        update={
            "scenarios": [
                s.model_copy(update={"probability": probs[s.label]})
                for s in opinion.scenarios
            ]
        }
    )
    return compute_expected_return(replaced, ctx, cfg)
