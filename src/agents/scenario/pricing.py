"""시나리오 가격 산정 — 결정적 코드 (LLM 호출 없음).

설계 근거: docs/03-scenario.md §4.1

핵심 결정 (docs §0, §1.4): LLM 은 확률·narrative·트리거만 생성하고 *가격 숫자는
만들지 않는다*. 본 모듈이 historical(52w high/low) + peer P/E + TTM EPS 를
config(`ScenarioPricingConfig`) 의 보수성 파라미터로 결합해 시나리오별 가격 →
확률 가중 expected return + variance 를 산출한다.

전부 순수 함수 — 같은 (ScenarioOpinion, ScenarioContext, config) → 같은
ExpectedReturn (computed_at 타임스탬프만 예외). numpy 미사용 (percentile 손수
구현, docs §4.1 v0.9).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from agents.scenario.pricing_config import ScenarioPricingConfig
from agents.scenario.schemas import ExpectedReturn, ScenarioContext, ScenarioOpinion

__all__ = [
    "percentile",
    "combine",
    "compute_bull_price",
    "compute_base_price",
    "compute_bear_price",
    "compute_expected_return",
]

logger = logging.getLogger(__name__)

_LABELS = ("bull", "base", "bear")


# ---------- 헬퍼 ----------


def percentile(xs: list[float], q: float) -> float:
    """순수 linear-interpolation percentile (numpy 'linear' 방식 정확 재현).

    docs §4.1 v0.9 — numpy 미사용 (peer_pe percentile 1곳 전용이라 신규 dep 회피,
    CLAUDE.md 콜드스타트 최소). q 는 0~100. 호출자가 빈 리스트 사전 차단.
    """
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    rank = (q / 100) * (len(s) - 1)
    lo = int(rank)
    if lo + 1 >= len(s):
        return s[-1]
    return s[lo] + (rank - lo) * (s[lo + 1] - s[lo])


def combine(
    a: float | None,
    b: float | None,
    mode: str,
    *,
    is_bear: bool = False,
) -> float | None:
    """둘 중 하나만 있으면 그 값. 둘 다 있으면 mode 에 따라 결합.
    둘 다 None 이면 None (호출자가 current_price fallback).

    docs §4.1 v0.3 — bear case 의미 분기:
    - bull (is_bear=False): conservative=min(a,b) (작은 상승), aggressive=max(a,b)
    - bear (is_bear=True):  conservative=max(a,b) (작은 하락), aggressive=min(a,b)
    balanced 는 두 케이스 모두 산술평균 — 의미 비대칭 없음.
    """
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    if mode == "balanced":
        return (a + b) / 2
    if is_bear:
        return max(a, b) if mode == "conservative" else min(a, b)
    return min(a, b) if mode == "conservative" else max(a, b)


def _peer_target(
    peer_pe: list[float],
    ttm_eps: float | None,
    q: float,
) -> float | None:
    """peer P/E percentile × TTM EPS. EPS 결측/음수·빈 peer_pe 면 None."""
    if ttm_eps is not None and ttm_eps > 0 and peer_pe:
        return percentile(peer_pe, q) * ttm_eps
    return None


# ---------- 시나리오별 가격 (docs §4.1) ----------


def compute_bull_price(
    current_price: float,
    return_52w_high: float | None,
    ttm_eps: float | None,
    peer_pe: list[float],
    cfg: ScenarioPricingConfig,
) -> float:
    historical_target = (
        current_price * (1 + return_52w_high)
        if return_52w_high is not None
        else None
    )
    peer_target = _peer_target(peer_pe, ttm_eps, cfg.peer_pe_bull_percentile)
    price = combine(historical_target, peer_target, mode=cfg.bull_aggressiveness)
    return price if price is not None else current_price  # v0.13 — 양쪽 결측 fallback


def compute_base_price(
    current_price: float,
    ttm_eps: float | None,
    peer_pe: list[float],
    cfg: ScenarioPricingConfig,
) -> float:
    peer_target = _peer_target(peer_pe, ttm_eps, cfg.peer_pe_base_percentile)
    if peer_target is None:
        return current_price  # fallback — fair value 산정 불가
    if cfg.base_price_cap_pct is None:
        return peer_target
    cap = current_price * (1 + cfg.base_price_cap_pct)
    return min(peer_target, cap)


def compute_bear_price(
    current_price: float,
    return_52w_low: float | None,
    ttm_eps: float | None,
    peer_pe: list[float],
    cfg: ScenarioPricingConfig,
) -> float:
    historical_target = (
        current_price * (1 + return_52w_low)
        if return_52w_low is not None
        else None
    )
    peer_target = _peer_target(peer_pe, ttm_eps, cfg.peer_pe_bear_percentile)
    price = combine(
        historical_target, peer_target, mode=cfg.bear_conservatism, is_bear=True
    )
    return price if price is not None else current_price  # v0.13 — 양쪽 결측 fallback


# ---------- 확률 보정 + 가격 순서 검증 (docs §4.1) ----------


def _apply_probability_cap(
    sc: dict[str, "object"],
    cfg: ScenarioPricingConfig,
) -> dict[str, float]:
    """bull/bear cap 적용 후 잉여 확률을 나머지 시나리오의 *원래 비율* 로 비례 분배.

    docs §4.1 v0.3. 예: bull=0.7, base=0.2, bear=0.1, bull_cap=0.5
        → 잉여 0.2 를 base:bear = 2:1 → bull=0.5, base=0.333, bear=0.167.
    bull 먼저, 이어서 bear (cfg 정의 순서). 나머지 합이 0 이면 균등 분배 (edge).
    """
    p = {label: sc[label].probability for label in _LABELS}  # type: ignore[attr-defined]
    for label, cap in (
        ("bull", cfg.bull_probability_cap),
        ("bear", cfg.bear_probability_cap),
    ):
        if cap is None or p[label] <= cap:
            continue
        excess = p[label] - cap
        p[label] = cap
        others = [k for k in p if k != label]
        total_others = sum(p[k] for k in others)
        if total_others > 0:
            for k in others:
                p[k] += excess * (p[k] / total_others)
        else:
            for k in others:
                p[k] += excess / len(others)
    return p


def _validate_price_order(prices: dict[str, float], symbol: str) -> list[str]:
    """bear ≤ base ≤ bull 검증, 위반 시 warning + flag (docs §4.1 v0.3).

    expected_return 산식엔 영향 없음 (확률 가중 합은 그대로). lineage 보존 우선
    — 자동 swap·보정 없음. 위반은 data_quality_flags 로 누적.
    """
    bull, base, bear = prices["bull"], prices["base"], prices["bear"]
    if bear <= base <= bull:
        return []
    flag = (
        f"price_order_violation: bear={bear:.2f}, base={base:.2f}, bull={bull:.2f}"
    )
    logger.warning(flag, extra={"symbol": symbol})
    return [flag]


# ---------- expected return + variance (docs §4.1) ----------


def compute_expected_return(
    opinion: ScenarioOpinion,
    ctx: ScenarioContext,
    cfg: ScenarioPricingConfig,
) -> ExpectedReturn:
    """시나리오 의견 + 가격 컨텍스트 + config → 결정적 ExpectedReturn.

    가격(bull/base/bear) 산정 → LLM 확률에 cap 적용 → 가격 순서 검증 →
    확률 가중 expected price + variance. current_price > 0 는 호출자가 보장
    (docs §9 — current_price ≤ 0 은 context 단계에서 스킵).
    """
    prices = {
        "bull": compute_bull_price(
            ctx.current_price, ctx.return_52w_high, ctx.ttm_eps, ctx.peer_pe, cfg
        ),
        "base": compute_base_price(
            ctx.current_price, ctx.ttm_eps, ctx.peer_pe, cfg
        ),
        "bear": compute_bear_price(
            ctx.current_price, ctx.return_52w_low, ctx.ttm_eps, ctx.peer_pe, cfg
        ),
    }
    sc = {s.label: s for s in opinion.scenarios}

    probs = _apply_probability_cap(sc, cfg)
    flags = _validate_price_order(prices, opinion.symbol)

    expected = sum(probs[lbl] * prices[lbl] for lbl in _LABELS)
    variance = sum(probs[lbl] * (prices[lbl] - expected) ** 2 for lbl in _LABELS)

    return ExpectedReturn(
        symbol=opinion.symbol,
        as_of_date=opinion.as_of_date,
        expected_price=expected,
        expected_return=(expected - ctx.current_price) / ctx.current_price,
        variance=variance,
        scenario_prices=prices,
        pricing_config=cfg,
        data_quality_flags=flags,
        scenario_opinion_s3_key=ctx.scenario_s3_key,
        computed_at=datetime.now(timezone.utc),
    )
