"""Bull/Bear 에이전트의 StockContext 조립 모듈.

설계 근거: docs/02-bull-bear.md §2.1, §2.1.1, §2.1.2, 부록 B

이 모듈의 역할:
1. compute_price_summary       — OHLCV pa.Table → PriceSummary
2. compute_fundamentals_timeseries — FMP 분기 응답 → FundamentalsTimeseries
3. build_context               — 위 두 + screened_to_context 합성 → StockContext
4. to_prompt_markdown          — 화이트리스트 직렬화 (LLM 프롬프트용)

순수 함수 — 네트워크/S3/FMP 호출 없음. 호출 측(Lambda 핸들러) 이 캐시에서
데이터를 읽어 인자로 주입한다 (CLAUDE.md "I/O 와 비즈니스 로직 분리").

§2.1.2 LLM 노출 정책의 강제 지점은 to_prompt_markdown 이다 — schema 는
모든 필드를 보존하지만 (audit 무결성), 직렬화 함수가 lineage 필드(run_id,
screening_s3_key, data_quality_flags) 를 LLM 프롬프트에서 화이트리스트로 차단.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from screening.schemas import ScreenedStock

from agents.bull_bear.mappers import screened_to_context
from agents.bull_bear.schemas import (
    FundamentalsTimeseries,
    PriceSummary,
    QuarterlyFigures,
    StockContext,
)

# ---------- 상수 ----------

DEFAULT_WINDOW_1Y = 252  # 미국 영업일
DEFAULT_WINDOW_6M = 126
TTM_QUARTERS = 4
MIN_BETA_OBSERVATIONS = 30  # 30영업일 미만이면 베타 신뢰성 낮아 None


# ---------- 가격 요약 ----------


def _filter_closes(ohlcv: pa.Table, as_of: date) -> list[float]:
    """as_of 이하 행의 adj_close 를 오름차순 list[float] 로 추출 (lookahead 차단)."""
    if ohlcv.num_rows == 0:
        return []
    as_of_scalar = pa.scalar(as_of, type=pa.date32())
    sliced = ohlcv.filter(pc.less_equal(ohlcv.column("date"), as_of_scalar))
    if sliced.num_rows == 0:
        return []
    sorted_indices = pc.sort_indices(sliced.column("date"))
    sorted_table = sliced.take(sorted_indices)
    return [float(v) for v in sorted_table.column("adj_close").to_pylist() if v is not None]


def _simple_return(closes: list[float], window: int) -> float | None:
    """closes[-1] / closes[-1-window] - 1. 데이터 부족·기준값 비양수면 None."""
    if len(closes) < window + 1:
        return None
    end = closes[-1]
    start = closes[-1 - window]
    if start <= 0:
        return None
    return end / start - 1


def _pct_from_extreme(closes: list[float], window: int, *, mode: str) -> float | None:
    """직전 window 일 내 high(또는 low) 대비 closes[-1] 의 비율 - 1."""
    if len(closes) < window:
        return None
    recent = closes[-window:]
    ref = max(recent) if mode == "high" else min(recent)
    if ref <= 0:
        return None
    return closes[-1] / ref - 1


def _log_returns(closes: list[float]) -> list[float]:
    """인접 close 의 로그수익률. 비양수 close 가 끼면 정상 시계열이 아님 → 빈 list."""
    returns: list[float] = []
    for prev, curr in zip(closes, closes[1:]):
        if prev <= 0 or curr <= 0:
            return []
        returns.append(math.log(curr / prev))
    return returns


def _ols_beta(y: list[float], x: list[float]) -> float | None:
    """단순 OLS — beta = Cov(y, x) / Var(x). x 분산 0 이면 None.

    plain Python 구현 — Lambda 번들에 numpy 추가하지 않기 위해 (단일 회귀라
    수치 안정성 critical 하지 않음).
    """
    n = len(x)
    if n != len(y) or n == 0:
        return None
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov_xy = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    if var_x == 0:
        return None
    return cov_xy / var_x


def _compute_beta_1y(
    stock_ohlcv: pa.Table,
    spy_ohlcv: pa.Table | None,
    as_of: date,
    window: int,
) -> float | None:
    """직전 window 영업일 일별 로그수익률 OLS 회귀 → 베타.

    SPY 미주입 또는 길이 미스매치 시 None — context_builder 호출 측이 SPY OHLCV
    캐시를 갖춘 경우에만 활성화. MVP 의 베타 신뢰성은 critical 하지 않음.
    """
    if spy_ohlcv is None:
        return None

    stock_closes = _filter_closes(stock_ohlcv, as_of)
    spy_closes = _filter_closes(spy_ohlcv, as_of)

    if len(stock_closes) < window + 1 or len(spy_closes) < window + 1:
        return None

    stock_recent = stock_closes[-(window + 1) :]
    spy_recent = spy_closes[-(window + 1) :]

    # 영업일 매칭은 호출 측 책임. 길이 미스매치는 인덱스 단순 매칭이 무의미하므로
    # 보수적으로 None — 정확도가 critical 해질 때 date inner-join 로직 추가.
    if len(stock_recent) != len(spy_recent):
        return None

    stock_returns = _log_returns(stock_recent)
    spy_returns = _log_returns(spy_recent)
    if len(stock_returns) < MIN_BETA_OBSERVATIONS:
        return None
    if not stock_returns or not spy_returns:
        return None

    return _ols_beta(stock_returns, spy_returns)


def compute_price_summary(
    ohlcv: pa.Table,
    *,
    as_of: date,
    spy_ohlcv: pa.Table | None = None,
    window_1y: int = DEFAULT_WINDOW_1Y,
    window_6m: int = DEFAULT_WINDOW_6M,
) -> PriceSummary:
    """OHLCV table → PriceSummary.

    동작:
    - as_of 일자(포함) 까지의 데이터만 사용 — lookahead 방지
    - 영업일 부족 시 해당 필드는 None (결측은 결측으로, docs §7)
    - spy_ohlcv 가 주어지면 1Y 베타 산출, 없으면 beta_1y=None
    - adj_close 사용 — 분할/배당 보정값
    """
    closes = _filter_closes(ohlcv, as_of)
    if not closes:
        return PriceSummary()

    return PriceSummary(
        return_1y=_simple_return(closes, window_1y),
        return_6m=_simple_return(closes, window_6m),
        pct_from_52w_high=_pct_from_extreme(closes, window_1y, mode="high"),
        pct_from_52w_low=_pct_from_extreme(closes, window_1y, mode="low"),
        beta_1y=_compute_beta_1y(ohlcv, spy_ohlcv, as_of, window_1y),
    )


# ---------- 펀더멘털 시계열 ----------


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_and_sort_quarters(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """FMP 분기 응답 행을 date desc 로 정렬 + date_obj 보강.

    'date' 키가 없거나 파싱 실패하면 해당 행 제외.
    원 dict 는 변경하지 않고 새 dict 로 복사.
    """
    parsed: list[dict[str, Any]] = []
    for row in rows:
        ds = row.get("date")
        if not isinstance(ds, str):
            continue
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
        except ValueError:
            continue
        parsed.append({**row, "_date_obj": d})
    parsed.sort(key=lambda r: r["_date_obj"], reverse=True)
    return parsed


def _sum_field(rows: list[dict[str, Any]], key: str) -> float | None:
    """rows 의 key 컬럼 합. 결측 1개라도 있으면 None (TTM 신뢰성)."""
    total = 0.0
    for row in rows:
        v = _to_float(row.get(key))
        if v is None:
            return None
        total += v
    return total


def _ttm_cagr(rows: list[dict[str, Any]], key: str, *, years: int) -> float | None:
    """TTM_now vs years 년 전 TTM 의 CAGR.

    rows 는 date desc 정렬됨을 가정. 필요 분기 수: 4 + years*4.
    음수/0 TTM 은 CAGR 정의상 무의미 — None.
    """
    window = TTM_QUARTERS
    offset = years * window
    if len(rows) < offset + window:
        return None

    ttm_now = _sum_field(rows[:window], key)
    ttm_old = _sum_field(rows[offset : offset + window], key)

    if ttm_now is None or ttm_old is None:
        return None
    if ttm_now <= 0 or ttm_old <= 0:
        return None
    return (ttm_now / ttm_old) ** (1 / years) - 1


def compute_fundamentals_timeseries(
    income_quarterly: list[dict[str, Any]],
    cashflow_quarterly: list[dict[str, Any]],
) -> FundamentalsTimeseries:
    """FMP 분기 income/cashflow 응답 → FundamentalsTimeseries.

    인자:
        income_quarterly: FMP `income-statement?period=quarter` 응답.
            각 행: 'date' (YYYY-MM-DD), 'revenue', 'epsDiluted' (stable 표기 —
            v3 표기 `epsdiluted` 는 fetch 시점에 normalize_income_rows 가 정규화).
        cashflow_quarterly: FMP `cash-flow-statement?period=quarter` 응답.
            각 행: 'date', 'freeCashFlow'.

    동작:
        1. 두 응답 각각 date desc 정렬
        2. 직전 4분기 — income 기준으로 추출, date 매칭으로 cashflow 의 fcf 결합
           (cashflow 누락 분기는 fcf=None — 결측은 결측으로)
        3. 5Y CAGR — TTM(직전 4분기 합) vs 5년 전 TTM. 분기 부족 시 None.
           EPS 는 분기 합 = TTM EPS. 음수 TTM 은 CAGR 무의미 → None.

    데이터 부족 시 quarters=[] + 모든 CAGR=None — schema 가 허용.
    """
    income_sorted = _parse_and_sort_quarters(income_quarterly)
    cashflow_sorted = _parse_and_sort_quarters(cashflow_quarterly)
    cashflow_by_date = {row["_date_obj"]: row for row in cashflow_sorted}

    quarters: list[QuarterlyFigures] = []
    for row in income_sorted[:TTM_QUARTERS]:
        cf_row = cashflow_by_date.get(row["_date_obj"])
        quarters.append(
            QuarterlyFigures(
                period_end=row["_date_obj"],
                revenue=_to_float(row.get("revenue")),
                eps_diluted=_to_float(row.get("epsDiluted")),
                fcf=_to_float(cf_row.get("freeCashFlow")) if cf_row else None,
            )
        )

    return FundamentalsTimeseries(
        quarters=quarters,
        revenue_cagr_5y=_ttm_cagr(income_sorted, "revenue", years=5),
        eps_cagr_5y=_ttm_cagr(income_sorted, "epsDiluted", years=5),
        fcf_cagr_5y=_ttm_cagr(cashflow_sorted, "freeCashFlow", years=5),
    )


# ---------- 합성 ----------


def build_context(
    stock: ScreenedStock,
    *,
    as_of_date: date,
    run_id: str,
    screening_s3_key: str,
    ohlcv: pa.Table | None,
    income_quarterly: list[dict[str, Any]] | None = None,
    cashflow_quarterly: list[dict[str, Any]] | None = None,
    spy_ohlcv: pa.Table | None = None,
) -> StockContext:
    """ScreenedStock + 캐시 데이터 → 완성된 StockContext.

    호출 측(Lambda 핸들러) 은 S3/FMP 캐시에서 ohlcv/statements 를 읽어 주입.
    데이터 누락(ohlcv=None 등) 시 해당 영역 필드는 결측으로 채워진 채 진행 —
    호출 가치 판단(스킵 여부) 은 상위 layer 가 별도 결정.
    """
    price_summary = (
        compute_price_summary(ohlcv, as_of=as_of_date, spy_ohlcv=spy_ohlcv)
        if ohlcv is not None
        else PriceSummary()
    )
    fundamentals = compute_fundamentals_timeseries(
        income_quarterly or [],
        cashflow_quarterly or [],
    )
    return screened_to_context(
        stock,
        as_of_date=as_of_date,
        run_id=run_id,
        screening_s3_key=screening_s3_key,
        price_summary=price_summary,
        fundamentals=fundamentals,
    )


# ---------- 화이트리스트 직렬화 ----------


def _fmt_num(v: float | None, decimals: int = 2, *, sign: bool = False) -> str:
    if v is None:
        return "n/a"
    fmt = f"{{:{'+' if sign else ''}.{decimals}f}}"
    return fmt.format(v)


def _fmt_pct(v: float | None, *, sign: bool = False, decimals: int = 2) -> str:
    if v is None:
        return "n/a"
    fmt = f"{{:{'+' if sign else ''}.{decimals}f}}%"
    return fmt.format(v * 100)


def _fmt_money(v: float | None) -> str:
    """$수치 포매팅. K/M/B/T 단위 축약 — 토큰 절감 + LLM 가독성."""
    if v is None:
        return "n/a"
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e12:
        return f"{sign}${a / 1e12:.2f}T"
    if a >= 1e9:
        return f"{sign}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.2f}M"
    if a >= 1e3:
        return f"{sign}${a / 1e3:.2f}K"
    return f"{sign}${a:.2f}"


def to_prompt_markdown(ctx: StockContext) -> str:
    """LLM 프롬프트로 들어가는 평문 직렬화 — 화이트리스트 (docs §2.1.2).

    명시적으로 제외되는 필드 (lineage):
      - run_id, screening_s3_key (audit/재현 — LLM 추론에 무가치)
      - data_quality_flags (회피적 답변 유도 위험)

    이 함수가 노출 정책의 *유일한* 강제 지점 — schema 는 모든 필드를 보존.
    프롬프트로 직접 들어가므로 결정적(deterministic) 출력 — 동일 입력 →
    동일 문자열 (테스트 가능성).
    """
    lines: list[str] = [
        f"# {ctx.symbol} — {ctx.company_name or 'n/a'}",
        f"- Sector: {ctx.sector or 'n/a'} / {ctx.sub_sector or 'n/a'}",
        f"- As-of: {ctx.as_of_date.isoformat()}",
        "",
        "## Screening Signals",
        "(통과 사유 컨텍스트 — 매수/매도 근거가 아니라 자체 데이터로 새로 추론할 것)",
        f"- Composite score: {_fmt_num(ctx.composite_score, 3)}",
        f"- Momentum z-score: {_fmt_num(ctx.momentum_z, 2, sign=True)}",
        f"- Value z-score: {_fmt_num(ctx.value_z, 2, sign=True)}",
        f"- P/E TTM: {_fmt_num(ctx.pe_ttm, 2)}",
        f"- EV/EBITDA TTM: {_fmt_num(ctx.ev_ebitda, 2)}",
        f"- FCF Yield TTM: {_fmt_pct(ctx.fcf_yield)}",
        "",
    ]

    if ctx.peer_context:
        lines.append("## Peer Context (sub_sector → sector 폴백, 최대 5개)")
        lines.append("| Symbol | P/E TTM | EV/EBITDA | FCF Yield |")
        lines.append("|---|---|---|---|")
        for peer in ctx.peer_context:
            lines.append(
                f"| {peer.symbol} | "
                f"{_fmt_num(peer.pe_ttm, 2)} | "
                f"{_fmt_num(peer.ev_ebitda, 2)} | "
                f"{_fmt_pct(peer.fcf_yield)} |"
            )
        lines.append("")

    ps = ctx.price_summary
    lines.extend(
        [
            "## Price Summary",
            f"- 1Y return: {_fmt_pct(ps.return_1y, sign=True)}",
            f"- 6M return: {_fmt_pct(ps.return_6m, sign=True)}",
            f"- From 52w high: {_fmt_pct(ps.pct_from_52w_high, sign=True)}",
            f"- From 52w low: {_fmt_pct(ps.pct_from_52w_low, sign=True)}",
            f"- Beta (1Y vs SPY): {_fmt_num(ps.beta_1y, 2)}",
            "",
        ]
    )

    fts = ctx.fundamentals
    if fts.quarters:
        lines.append("## Fundamentals — Last 4 Quarters")
        lines.append("| Period end | Revenue | Diluted EPS | FCF |")
        lines.append("|---|---|---|---|")
        for q in fts.quarters:
            lines.append(
                f"| {q.period_end.isoformat()} | "
                f"{_fmt_money(q.revenue)} | "
                f"{_fmt_num(q.eps_diluted, 2)} | "
                f"{_fmt_money(q.fcf)} |"
            )
        lines.append("")

    cagr_parts: list[str] = []
    if fts.revenue_cagr_5y is not None:
        cagr_parts.append(f"Revenue {_fmt_pct(fts.revenue_cagr_5y, sign=True)}")
    if fts.eps_cagr_5y is not None:
        cagr_parts.append(f"EPS {_fmt_pct(fts.eps_cagr_5y, sign=True)}")
    if fts.fcf_cagr_5y is not None:
        cagr_parts.append(f"FCF {_fmt_pct(fts.fcf_cagr_5y, sign=True)}")
    if cagr_parts:
        lines.append(f"5Y CAGR — {', '.join(cagr_parts)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
