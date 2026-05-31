"""시나리오 에이전트의 ScenarioContext 조립 + 프롬프트 직렬화.

설계 근거: docs/03-scenario.md §2.1, §3.3, §9

이 모듈의 역할:
1. build_context       — ScreenedStock + Bull/Bear 의견 2개 + 캐시 데이터(OHLCV,
   분기 income) → 완성된 ScenarioContext (가격 컨텍스트 산출 포함)
2. to_prompt_markdown  — 화이트리스트 직렬화 (LLM 프롬프트용, lineage 미노출)

순수 함수 — 네트워크/S3/FMP 호출 없음. 호출 측(Lambda 핸들러, #9) 이 S3 에서
Bull/Bear opinion 을, 캐시에서 OHLCV/분기 statement 를 읽어 주입한다
(CLAUDE.md "I/O 와 비즈니스 로직 분리").

가격 컨텍스트 산출 (docs §2.1):
- current_price       = as_of 이하 최신 adj_close (lookahead 차단)
- return_52w_high     = (52w high - current) / current  (≥ 0, current 기준)
- return_52w_low      = (52w low - current) / current   (≤ 0)
- ttm_eps             = 직전 4분기 epsdiluted 합 (분기 부족/결측 시 None)
- peer_pe             = ScreenedStock.peer_context 의 양수 pe_ttm 리스트

LLM 노출 정책 (docs §3.3): identity·가격 컨텍스트·Bull/Bear 의견 본문은
to_prompt_markdown 으로 노출, lineage (run_id/scenario_s3_key/bullbear_s3_keys/
data_quality_flags) 와 opinion 메타(model/tokens/cost) 는 화이트리스트로 차단.
이 분리의 *유일한* 강제 지점이 to_prompt_markdown — schema 는 전부 보존.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from screening.schemas import ScreenedStock

from agents.bull_bear.schemas import BullBearOpinion
from agents.scenario.schemas import ScenarioContext

__all__ = [
    "ScenarioContextError",
    "build_context",
    "to_prompt_markdown",
]

WINDOW_52W = 252  # 미국 영업일
TTM_QUARTERS = 4


class ScenarioContextError(ValueError):
    """ScenarioContext 조립 불가 — 명백한 데이터 오류 (current_price ≤ 0,
    OHLCV 결측, Bull/Bear stance 불일치 등). 종목 스킵 (docs §9)."""


# ---------- OHLCV → 가격 컨텍스트 ----------


def _sorted_closes(ohlcv: pa.Table | None, as_of: date) -> list[float]:
    """as_of 이하 행의 adj_close 를 date 오름차순 list 로 추출 (lookahead 차단)."""
    if ohlcv is None or ohlcv.num_rows == 0:
        return []
    as_of_scalar = pa.scalar(as_of, type=pa.date32())
    sliced = ohlcv.filter(pc.less_equal(ohlcv.column("date"), as_of_scalar))
    if sliced.num_rows == 0:
        return []
    sorted_table = sliced.take(pc.sort_indices(sliced.column("date")))
    return [
        float(v)
        for v in sorted_table.column("adj_close").to_pylist()
        if v is not None
    ]


def _price_context(
    ohlcv: pa.Table | None,
    as_of: date,
) -> tuple[float | None, float | None, float | None]:
    """(current_price, return_52w_high, return_52w_low). 결측이면 (None, None, None).

    return_52w_high/low 는 *current 기준* — high 는 ≥ current 이므로 ≥ 0,
    low 는 ≤ current 이므로 ≤ 0 (docs §2.1).
    """
    closes = _sorted_closes(ohlcv, as_of)
    if not closes:
        return None, None, None
    current = closes[-1]
    if current <= 0:
        return current, None, None  # build_context 가 ScenarioContextError
    recent = closes[-WINDOW_52W:]
    high = max(recent)
    low = min(recent)
    return current, (high - current) / current, (low - current) / current


# ---------- 분기 income → TTM EPS ----------


def _ttm_eps(income_quarterly: list[dict[str, Any]]) -> float | None:
    """직전 4분기 epsdiluted 합 (date desc). 분기 부족/결측이면 None."""
    parsed: list[tuple[date, dict[str, Any]]] = []
    for row in income_quarterly:
        ds = row.get("date")
        if not isinstance(ds, str):
            continue
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
        except ValueError:
            continue
        parsed.append((d, row))
    if len(parsed) < TTM_QUARTERS:
        return None
    parsed.sort(key=lambda x: x[0], reverse=True)
    total = 0.0
    for _, row in parsed[:TTM_QUARTERS]:
        v = row.get("epsdiluted")
        try:
            total += float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None  # 결측 분기 1개라도 있으면 TTM 신뢰 불가
    return total


def _peer_pe(stock: ScreenedStock) -> list[float]:
    """ScreenedStock.peer_context 에서 양수 pe_ttm 만 추출 (정렬 무관 — 코드가 percentile)."""
    return [
        p.pe_ttm
        for p in stock.peer_context
        if p.pe_ttm is not None and p.pe_ttm > 0
    ]


# ---------- 합성 ----------


def build_context(
    stock: ScreenedStock,
    bull_opinion: BullBearOpinion,
    bear_opinion: BullBearOpinion,
    *,
    as_of_date: date,
    run_id: str,
    scenario_s3_key: str,
    bullbear_s3_keys: dict[str, str],
    ohlcv: pa.Table | None,
    income_quarterly: list[dict[str, Any]] | None = None,
) -> ScenarioContext:
    """ScreenedStock + Bull/Bear 의견 + 캐시 데이터 → ScenarioContext.

    호출 측(#9 lambda_core) 이 S3 에서 opinion 2개, 캐시에서 OHLCV/income 을 읽어
    주입. current_price 산정 불가(OHLCV 결측/≤0) 또는 stance 불일치 시
    ScenarioContextError → 종목 스킵 (docs §9).
    """
    if bull_opinion.stance != "bull" or bear_opinion.stance != "bear":
        raise ScenarioContextError(
            f"{stock.symbol}: stance 불일치 "
            f"(bull={bull_opinion.stance}, bear={bear_opinion.stance})"
        )
    if not (bull_opinion.symbol == bear_opinion.symbol == stock.symbol):
        raise ScenarioContextError(
            f"symbol 불일치: stock={stock.symbol}, "
            f"bull={bull_opinion.symbol}, bear={bear_opinion.symbol}"
        )

    current_price, return_52w_high, return_52w_low = _price_context(ohlcv, as_of_date)
    if current_price is None or current_price <= 0:
        raise ScenarioContextError(
            f"{stock.symbol}: current_price 산정 불가 "
            f"(OHLCV 결측 또는 비정상: {current_price})"
        )

    return ScenarioContext(
        symbol=stock.symbol,
        company_name=stock.company_name,
        sector=stock.sector,
        sub_sector=stock.sub_sector,
        as_of_date=as_of_date,
        bull_opinion=bull_opinion,
        bear_opinion=bear_opinion,
        current_price=current_price,
        ttm_eps=_ttm_eps(income_quarterly or []),
        peer_pe=_peer_pe(stock),
        return_52w_high=return_52w_high,
        return_52w_low=return_52w_low,
        run_id=run_id,
        scenario_s3_key=scenario_s3_key,
        bullbear_s3_keys=bullbear_s3_keys,  # type: ignore[arg-type]
        data_quality_flags=list(stock.data_quality_flags),
    )


# ---------- 화이트리스트 직렬화 (docs §3.3) ----------


def _fmt_num(v: float | None, decimals: int = 2, *, sign: bool = False) -> str:
    if v is None:
        return "n/a"
    return f"{{:{'+' if sign else ''}.{decimals}f}}".format(v)


def _fmt_pct(v: float | None, *, sign: bool = False) -> str:
    if v is None:
        return "n/a"
    return f"{{:{'+' if sign else ''}.2f}}%".format(v * 100)


def _fmt_money(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"${v:.2f}"


def _opinion_lines(op: BullBearOpinion, title: str) -> list[str]:
    """Bull/Bear 의견 본문 직렬화 — summary/arguments/key_risks 만.

    opinion 메타(model/input_tokens/output_tokens/cost_usd) 는 LLM 추론에
    무가치 → 화이트리스트 차단.
    """
    lines = [f"## {title}", op.summary, "", "Arguments:"]
    lines.extend(
        f"- [{a.confidence}] {a.claim} — {a.evidence}" for a in op.arguments
    )
    lines.append("")
    lines.append("Key risks to thesis:")
    lines.extend(f"- {r}" for r in op.key_risks_to_thesis)
    lines.append("")
    return lines


def to_prompt_markdown(ctx: ScenarioContext) -> str:
    """LLM 프롬프트로 들어가는 평문 직렬화 — 화이트리스트 (docs §3.3).

    명시적으로 제외되는 필드 (lineage):
      - run_id, scenario_s3_key, bullbear_s3_keys (audit/재현 — 추론에 무가치)
      - data_quality_flags (회피적 답변 유도 위험)
    opinion 메타(model/tokens/cost) 도 제외 (_opinion_lines).

    이 함수가 노출 정책의 *유일한* 강제 지점 — 결정적 출력 (동일 입력 →
    동일 문자열, 테스트 가능).
    """
    peer_str = (
        ", ".join(_fmt_num(p, 1) for p in ctx.peer_pe) if ctx.peer_pe else "n/a"
    )
    lines: list[str] = [
        f"# {ctx.symbol} — {ctx.company_name or 'n/a'}",
        f"- Sector: {ctx.sector or 'n/a'} / {ctx.sub_sector or 'n/a'}",
        f"- As-of: {ctx.as_of_date.isoformat()}",
        "",
        "## Price Context",
        f"- Current price: {_fmt_money(ctx.current_price)}",
        f"- TTM EPS: {_fmt_num(ctx.ttm_eps)}",
        f"- Peer P/E ({len(ctx.peer_pe)}): {peer_str}",
        f"- Return to 52w high: {_fmt_pct(ctx.return_52w_high, sign=True)}",
        f"- Return to 52w low: {_fmt_pct(ctx.return_52w_low, sign=True)}",
        "",
    ]
    lines.extend(_opinion_lines(ctx.bull_opinion, "Bull Opinion"))
    lines.extend(_opinion_lines(ctx.bear_opinion, "Bear Opinion"))
    return "\n".join(lines).rstrip() + "\n"
