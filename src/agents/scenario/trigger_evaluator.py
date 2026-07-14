"""트리거 자동 검증 + 확률 calibration (Self-Verification).

설계 근거: docs/03-scenario.md §7, §1.4.2

이 모듈의 역할 (분기 발표 후 batch — #13 이 활성화):
1. evaluate_trigger     — InvalidationTrigger + 분기 statement → TriggerEvaluation
   (met / requires_human_review). tripwire = 다음 분기 발표 1회 평가 (§7.1 v0.5).
2. realized_scenario    — 실제 가격을 scenario_prices 중점 bin 으로 분류 (§7.2 v0.10)
3. brier_score          — 확률 calibration 측정 (§1.4.2 #1)

순수 함수 — 네트워크/S3/FMP 호출 없음. 호출 측(batch)이 FMP 캐시에서 분기
statement 를 읽어 주입 (CLAUDE.md I/O 분리). #1 InvalidationTrigger 에만 의존
— agent/pricing 경로와 독립 (docs §11 #7 노트).

observe-only 경계 (docs §7.2 E): 평가는 회고 calibration 전용 — 트리거 met 이
M3 매매·재분석에 자동 피드백되지 않음 (CHARTER §6 매매는 룰 기반).

P2-D (docs §12.2): guidance_change 자동화는 *human-review fallback 으로 시작*
— transcript 텍스트 분석 자동화는 M3 후반 (§12.2/§12.3).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

from agents.scenario.schemas import InvalidationTrigger

__all__ = [
    "TriggerEvaluation",
    "evaluate_trigger",
    "realized_scenario",
    "brier_score",
]

_LABELS = ("bull", "base", "bear")

# 자동 측정 불가 — 인간 검토 (docs §2.4, §7.3)
_HUMAN_REVIEW_METRICS = frozenset({"guidance_change", "peer_announcement"})
# net_debt_yoy 는 이 sub_sector 에서 부채가 비즈니스 모델 — 의미 없음 (docs §3.2, §7.3)
_NET_DEBT_EXEMPT_SECTORS = frozenset({"Financials", "Utilities"})


class TriggerEvaluation(BaseModel):
    """단일 (시나리오, 트리거) 의 분기 발표 후 평가 결과 (docs §7.1 v0.10).

    met: True=무효화 발동 / False=미발동 / None=평가 불가(데이터 부족) 또는
    인간 검토 필요. requires_human_review 가 True 면 met 은 항상 None.
    """

    symbol: str
    scenario_label: Literal["bull", "base", "bear"]
    metric: str
    actual: float | None = None          # 자동 측정값 (percent)
    threshold: float | None = None       # 원본 트리거 threshold
    met: bool | None = None
    requires_human_review: bool = False
    evaluated_quarter: str               # 평가 기준 분기 (예: "2026-Q2")
    scenario_s3_key: str                 # 원본 ScenarioOpinion lineage
    evaluated_at: datetime


# ---------- 데이터 헬퍼 ----------


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sorted_desc(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """FMP 분기 응답을 date desc 정렬 (최신 우선). 'date' 파싱 실패 행 제외."""
    parsed: list[tuple[date, dict[str, Any]]] = []
    for row in rows:
        ds = row.get("date")
        if not isinstance(ds, str):
            continue
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
        except ValueError:
            continue
        parsed.append((d, row))
    parsed.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in parsed]


def _at(rows: list[dict[str, Any]], idx: int, field: str) -> float | None:
    """rows[idx][field] as float. 범위 밖이면 None."""
    if idx >= len(rows):
        return None
    return _to_float(rows[idx].get(field))


def _pct_change(now: float | None, prior: float | None) -> float | None:
    """(now/prior - 1)*100. prior 비양수/결측이면 None (성장률 정의 불가)."""
    if now is None or prior is None or prior <= 0:
        return None
    return (now / prior - 1) * 100


def _margin_at(rows: list[dict[str, Any]], idx: int, numerator: str) -> float | None:
    """rows[idx] 의 numerator/revenue 마진. revenue 비양수/결측이면 None."""
    num = _at(rows, idx, numerator)
    rev = _at(rows, idx, "revenue")
    if num is None or rev is None or rev <= 0:
        return None
    return num / rev


def _net_debt_at(rows: list[dict[str, Any]], idx: int) -> float | None:
    """rows[idx] 의 totalDebt - cashAndShortTermInvestments."""
    debt = _at(rows, idx, "totalDebt")
    cash = _at(rows, idx, "cashAndShortTermInvestments")
    if debt is None or cash is None:
        return None
    return debt - cash


def _quarter_label(rows: list[dict[str, Any]]) -> str:
    """최신 분기의 'YYYY-QN' 라벨. 데이터 없으면 'unknown'."""
    sorted_rows = _sorted_desc(rows)
    if not sorted_rows:
        return "unknown"
    ds = sorted_rows[0]["date"]
    d = datetime.strptime(ds, "%Y-%m-%d").date()
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


# ---------- metric 평가 (순수 코어) ----------


def _metric_actual(
    metric: str,
    *,
    income: list[dict[str, Any]],
    cashflow: list[dict[str, Any]],
    balance: list[dict[str, Any]],
    earnings: list[dict[str, Any]],
) -> float | None:
    """metric 의 자동 측정값 (percent). 평가 불가(데이터 부족)면 None.

    YoY = 최신 vs 4분기 전 / QoQ = 최신 vs 직전. margin·net_debt 는 % change.
    earnings_surprise 는 actual vs estimate.
    """
    inc = _sorted_desc(income)
    cf = _sorted_desc(cashflow)
    bs = _sorted_desc(balance)

    if metric == "revenue_yoy":
        return _pct_change(_at(inc, 0, "revenue"), _at(inc, 4, "revenue"))
    if metric == "revenue_qoq":
        return _pct_change(_at(inc, 0, "revenue"), _at(inc, 1, "revenue"))
    if metric == "eps_yoy":
        return _pct_change(_at(inc, 0, "epsDiluted"), _at(inc, 4, "epsDiluted"))
    if metric == "fcf_yoy":
        return _pct_change(_at(cf, 0, "freeCashFlow"), _at(cf, 4, "freeCashFlow"))
    if metric == "gross_margin_yoy":
        return _pct_change(_margin_at(inc, 0, "grossProfit"), _margin_at(inc, 4, "grossProfit"))
    if metric == "operating_margin_yoy":
        return _pct_change(_margin_at(inc, 0, "operatingIncome"), _margin_at(inc, 4, "operatingIncome"))
    if metric == "net_debt_yoy":
        return _pct_change(_net_debt_at(bs, 0), _net_debt_at(bs, 4))
    if metric == "earnings_surprise":
        es = _sorted_desc(earnings)
        return _pct_change(_at(es, 0, "actualEarningResult"), _at(es, 0, "estimatedEarning"))
    return None


def _check(actual: float, direction: str, threshold: float) -> bool:
    """방향·임계 비교. less_than: actual < threshold / greater_than: actual > threshold."""
    if direction == "less_than":
        return actual < threshold
    return actual > threshold


# ---------- 공개 진입점 ----------


def evaluate_trigger(
    trigger: InvalidationTrigger,
    *,
    symbol: str,
    scenario_label: Literal["bull", "base", "bear"],
    scenario_s3_key: str,
    evaluated_at: datetime,
    income_quarterly: list[dict[str, Any]] | None = None,
    cashflow_quarterly: list[dict[str, Any]] | None = None,
    balance_quarterly: list[dict[str, Any]] | None = None,
    earnings_surprises: list[dict[str, Any]] | None = None,
    sub_sector: str | None = None,
) -> TriggerEvaluation:
    """트리거를 다음 분기 발표 데이터로 평가 (tripwire, docs §7.1).

    인간 검토 분기:
    - guidance_change / peer_announcement — 정성 metric (P2-D: human fallback 시작)
    - net_debt_yoy + Financials/Utilities — sub_sector 가드 (시스템 프롬프트
      위반 케이스 감지, docs §7.3)
    그 외 정량 metric 은 자동 평가; 데이터 부족 시 met=None (human review 아님).
    """
    income = income_quarterly or []
    cashflow = cashflow_quarterly or []
    balance = balance_quarterly or []
    earnings = earnings_surprises or []

    def _build(
        *, actual: float | None, met: bool | None, human: bool, quarter_rows: list[dict[str, Any]]
    ) -> TriggerEvaluation:
        return TriggerEvaluation(
            symbol=symbol,
            scenario_label=scenario_label,
            metric=trigger.metric,
            actual=actual,
            threshold=trigger.threshold,
            met=met,
            requires_human_review=human,
            evaluated_quarter=_quarter_label(quarter_rows),
            scenario_s3_key=scenario_s3_key,
            evaluated_at=evaluated_at,
        )

    # 1. 정성 metric → 인간 검토
    if trigger.metric in _HUMAN_REVIEW_METRICS:
        return _build(actual=None, met=None, human=True, quarter_rows=income)

    # 2. net_debt_yoy sub_sector 가드
    if trigger.metric == "net_debt_yoy" and sub_sector in _NET_DEBT_EXEMPT_SECTORS:
        return _build(actual=None, met=None, human=True, quarter_rows=balance)

    # 3. 정량 metric 자동 평가
    quarter_rows = (
        earnings if trigger.metric == "earnings_surprise"
        else balance if trigger.metric == "net_debt_yoy"
        else cashflow if trigger.metric == "fcf_yoy"
        else income
    )
    actual = _metric_actual(
        trigger.metric, income=income, cashflow=cashflow, balance=balance, earnings=earnings
    )
    if actual is None or trigger.threshold is None:
        return _build(actual=actual, met=None, human=False, quarter_rows=quarter_rows)

    met = _check(actual, trigger.direction, trigger.threshold)
    return _build(actual=actual, met=met, human=False, quarter_rows=quarter_rows)


# ---------- 확률 calibration (docs §7.2 v0.10, §1.4.2 #1) ----------


def realized_scenario(
    actual_price: float,
    prices: dict[str, float],
) -> Literal["bull", "base", "bear"]:
    """실제 가격을 scenario_prices 중점 bin 으로 분류 (docs §7.2 v0.10).

    앵커는 호출 측이 정함 (다음 분기 발표일 종가 — tripwire 동일 시간축).
    가격 순서 위반 종목(data_quality_flags)은 호출 측이 calibration 표본에서
    사전 제외 — bin 경계 신뢰 불가.
    """
    bull, base, bear = prices["bull"], prices["base"], prices["bear"]
    if actual_price >= (bull + base) / 2:
        return "bull"
    if actual_price <= (base + bear) / 2:
        return "bear"
    return "base"


def brier_score(probabilities: dict[str, float], realized: str) -> float:
    """다중 클래스 Brier score (docs §1.4.2 #1).

    BS = Σ_s (probability_s - 1_{s == realized})². 0(완벽)~2(최악). uniform
    predictor(각 0.33) 는 realized 1개 기준 ≈ 0.667. 임계 < 0.25 (§1.4.2).
    """
    return sum(
        (probabilities.get(label, 0.0) - (1.0 if label == realized else 0.0)) ** 2
        for label in _LABELS
    )
