"""트리거 자동 검증 + calibration 단위 테스트.

순수 함수 — 네트워크/S3/FMP 호출 없음. 픽스처 분기 데이터 입력.
설계 근거: docs/03-scenario.md §7, §1.4.2
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.scenario.schemas import InvalidationTrigger
from agents.scenario.trigger_evaluator import (
    brier_score,
    evaluate_trigger,
    realized_scenario,
)

EVAL_AT = datetime(2026, 8, 1, tzinfo=timezone.utc)

# 5분기 income (date desc 아님 — 코드가 정렬). Q0 최신.
_DATES = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"]


def _income() -> list[dict[str, object]]:
    rev = [110, 105, 103, 102, 100]
    gp = [49.5, 47, 45, 42, 40]       # margin: Q0=0.45, Q4=0.40
    oi = [22, 21, 20, 19, 18]
    eps = [2.2, 2.1, 2.05, 2.02, 2.0]
    return [
        {"date": d, "revenue": rev[i], "grossProfit": gp[i], "operatingIncome": oi[i], "epsDiluted": eps[i]}
        for i, d in enumerate(_DATES)
    ]


def _cashflow() -> list[dict[str, object]]:
    fcf = [55, 53, 52, 51, 50]
    return [{"date": d, "freeCashFlow": fcf[i]} for i, d in enumerate(_DATES)]


def _balance() -> list[dict[str, object]]:
    debt = [120, 118, 115, 112, 110]
    cash = [20, 22, 25, 28, 30]
    return [{"date": d, "totalDebt": debt[i], "cashAndShortTermInvestments": cash[i]} for i, d in enumerate(_DATES)]


def _earnings() -> list[dict[str, object]]:
    return [{"date": "2026-06-30", "actualEarningResult": 2.2, "estimatedEarning": 2.0}]


def _trigger(metric: str, direction: str = "less_than", threshold: float | None = 5.0) -> InvalidationTrigger:
    unit = "qualitative" if metric in {"guidance_change", "peer_announcement"} else "percent"
    thr = None if unit == "qualitative" else threshold
    return InvalidationTrigger(
        metric=metric, direction=direction, threshold=thr,  # type: ignore[arg-type]
        threshold_unit=unit, description="trigger description text",
    )


def _eval(trigger: InvalidationTrigger, **kw: object):
    base: dict[str, object] = {
        "symbol": "AAPL",
        "scenario_label": "bull",
        "scenario_s3_key": "scenarios/dt=2026-05-04/symbol=AAPL.json",
        "evaluated_at": EVAL_AT,
        "income_quarterly": _income(),
        "cashflow_quarterly": _cashflow(),
        "balance_quarterly": _balance(),
        "earnings_surprises": _earnings(),
    }
    base.update(kw)
    return evaluate_trigger(trigger, **base)  # type: ignore[arg-type]


# ---------- 정량 metric actual 산출 ----------


@pytest.mark.parametrize(
    "metric, expected_actual",
    [
        ("revenue_yoy", pytest.approx(10.0)),   # 110/100 - 1
        ("revenue_qoq", pytest.approx((110 / 105 - 1) * 100)),
        ("eps_yoy", pytest.approx((2.2 / 2.0 - 1) * 100)),
        ("fcf_yoy", pytest.approx(10.0)),        # 55/50 - 1
        ("gross_margin_yoy", pytest.approx((0.45 / 0.40 - 1) * 100)),  # +12.5%
        ("net_debt_yoy", pytest.approx(25.0)),   # (100/80 - 1)*100
        ("earnings_surprise", pytest.approx((2.2 / 2.0 - 1) * 100)),   # +10%
    ],
)
def test_metric_actual(metric: str, expected_actual: object) -> None:
    ev = _eval(_trigger(metric))
    assert ev.actual == expected_actual


# ---------- 방향·임계 met 판정 ----------


def test_met_less_than() -> None:
    # revenue_yoy 실제 +10%, less_than 5 → 10 < 5 = False
    assert _eval(_trigger("revenue_yoy", "less_than", 5.0)).met is False


def test_met_greater_than() -> None:
    # +10%, greater_than 5 → True
    assert _eval(_trigger("revenue_yoy", "greater_than", 5.0)).met is True


def test_met_boundary() -> None:
    # +10%, less_than 10 → 10 < 10 = False (경계)
    assert _eval(_trigger("revenue_yoy", "less_than", 10.0)).met is False


# ---------- 인간 검토 ----------


@pytest.mark.parametrize("metric", ["guidance_change", "peer_announcement"])
def test_qualitative_requires_human_review(metric: str) -> None:
    ev = _eval(_trigger(metric))
    assert ev.requires_human_review is True
    assert ev.met is None
    assert ev.actual is None


def test_net_debt_financials_guard() -> None:
    ev = _eval(_trigger("net_debt_yoy"), sub_sector="Financials")
    assert ev.requires_human_review is True
    assert ev.met is None


def test_net_debt_normal_sector_evaluated() -> None:
    ev = _eval(_trigger("net_debt_yoy"), sub_sector="Technology")
    assert ev.requires_human_review is False
    assert ev.actual == 25.0


# ---------- 데이터 부족 ----------


def test_insufficient_quarters_met_none() -> None:
    # 4분기 미만 → YoY([4]) 불가 → met=None, human=False
    ev = _eval(_trigger("revenue_yoy"), income_quarterly=_income()[:3])
    assert ev.met is None
    assert ev.actual is None
    assert ev.requires_human_review is False


def test_negative_prior_met_none() -> None:
    # prior revenue 0 → 성장률 정의 불가
    inc = _income()
    inc[4]["revenue"] = 0
    ev = _eval(_trigger("revenue_yoy"), income_quarterly=inc)
    assert ev.actual is None
    assert ev.met is None


# ---------- 메타 ----------


def test_evaluated_quarter_label() -> None:
    # 최신 income 2026-06-30 → Q2
    assert _eval(_trigger("revenue_yoy")).evaluated_quarter == "2026-Q2"


def test_lineage_preserved() -> None:
    ev = _eval(_trigger("revenue_yoy"))
    assert ev.symbol == "AAPL"
    assert ev.scenario_label == "bull"
    assert ev.scenario_s3_key == "scenarios/dt=2026-05-04/symbol=AAPL.json"
    assert ev.evaluated_at == EVAL_AT


# ---------- realized_scenario (docs §7.2) ----------


@pytest.mark.parametrize(
    "actual_price, expected",
    [
        (140.0, "bull"),   # >= (130+100)/2 = 115
        (115.0, "bull"),   # 경계 (>=)
        (110.0, "base"),
        (100.0, "base"),
        (90.0, "bear"),    # <= (100+80)/2 = 90
        (70.0, "bear"),
    ],
)
def test_realized_scenario_bins(actual_price: float, expected: str) -> None:
    prices = {"bull": 130.0, "base": 100.0, "bear": 80.0}
    assert realized_scenario(actual_price, prices) == expected


# ---------- brier_score (docs §1.4.2 #1) ----------


def test_brier_perfect() -> None:
    # bull 확률 1.0, realized bull → 0
    assert brier_score({"bull": 1.0, "base": 0.0, "bear": 0.0}, "bull") == 0.0


def test_brier_worst() -> None:
    # bull 1.0 인데 realized bear → (1-0)²+(0-0)²+(0-1)² = 2.0
    assert brier_score({"bull": 1.0, "base": 0.0, "bear": 0.0}, "bear") == pytest.approx(2.0)


def test_brier_typical() -> None:
    # 0.4/0.45/0.15, realized base → 0.4²+(0.45-1)²+0.15² = 0.16+0.3025+0.0225 = 0.485
    assert brier_score({"bull": 0.4, "base": 0.45, "bear": 0.15}, "base") == pytest.approx(0.485)


def test_brier_uniform() -> None:
    # 각 1/3, realized 1개 → 2*(1/3)² + (2/3)² = 0.2222 + 0.4444 = 0.6667
    bs = brier_score({"bull": 1 / 3, "base": 1 / 3, "bear": 1 / 3}, "bull")
    assert bs == pytest.approx(2 / 3, abs=1e-6)
