"""골든 케이스 — Bull/Bear 에이전트 실제 호출 + 스냅샷 저장.

설계 근거: docs/02-bull-bear.md §8.3, §9 #6, §10

대상 4종목 (sector 다양성):
- AAPL: Tech / Consumer Electronics — 모멘텀+밸류 균형
- XOM:  Energy / Integrated O&G — 사이클·배당·고 FCF Yield
- NVDA: Tech / Semiconductors — 모멘텀 익스트림, 밸류 부담
- JPM:  Financials / Diversified Banks — sector-specific 팩터 보강 효과 평가
        (docs §10: EV/EBITDA·FCF Yield 가 구조적으로 왜곡되는 케이스)

비용 추정: 4종목 × 2 stance × ~$0.02/호출 ≈ **$0.16** (Sonnet 4.6, temperature=0).

실행:
  PYTHONPATH=src ANTHROPIC_API_KEY=sk-... .venv/bin/python scripts/run_bullbear_golden.py

옵션:
  --symbols AAPL,JPM        # 일부 종목만
  --stances bull            # bull 만 (또는 bear)
  --output-dir tests/golden/bullbear   # 기본값
  --dry-run                 # 실제 호출 없이 fixture/프롬프트만 인쇄

스냅샷 형식: tests/golden/bullbear/{symbol}_{stance}.json
  {
    "opinion": {... BullBearOpinion model_dump ...},
    "attempts": [{... CallAttempt 들 ...}],
    "prompt_user": "<직렬화된 user 프롬프트 — 사후 비교용>"
  }
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Iterable

# scripts/ 에서 src/ import 가능하도록 path 보강
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.bull_bear.agent import (  # noqa: E402
    BullBearAgentError,
    CallResult,
    context_input_hash,
    run_bullbear_agent,
)
from agents.bull_bear.context_builder import to_prompt_markdown  # noqa: E402
from agents.bull_bear.schemas import (  # noqa: E402
    FundamentalsTimeseries,
    PeerComparable,
    PriceSummary,
    QuarterlyFigures,
    StockContext,
)

DEFAULT_AS_OF = date(2026, 4, 27)
DEFAULT_RUN_ID = "golden-2026-04-27"
DEFAULT_S3_KEY = "screening/dt=2026-04-27/result.json"

# ---------- Fixture: 4종목 StockContext ----------
# 운영 데이터를 그대로 쓰지 않는 이유: 스냅샷은 결정적 입력이어야 회귀 비교가
# 의미 있다. 시점에 따라 OHLCV/펀더멘털이 변하는 운영 데이터로는 매 실행마다
# 입력이 달라져 LLM 응답 변동이 입력 변동인지 모델 변동인지 분리 불가.

GOLDEN_FIXTURES: dict[str, StockContext] = {
    "AAPL": StockContext(
        symbol="AAPL",
        company_name="Apple Inc.",
        sector="Technology",
        sub_sector="Consumer Electronics",
        as_of_date=DEFAULT_AS_OF,
        composite_score=1.42,
        momentum_z=0.95,
        value_z=-0.30,
        pe_ttm=29.5,
        ev_ebitda=22.0,
        fcf_yield=0.038,
        peer_context=[
            PeerComparable(symbol="MSFT", pe_ttm=33.0, ev_ebitda=23.5, fcf_yield=0.030),
            PeerComparable(symbol="GOOGL", pe_ttm=24.0, ev_ebitda=17.0, fcf_yield=0.045),
            PeerComparable(symbol="META", pe_ttm=26.0, ev_ebitda=15.5, fcf_yield=0.050),
            PeerComparable(symbol="ORCL", pe_ttm=31.0, ev_ebitda=21.0, fcf_yield=0.025),
        ],
        price_summary=PriceSummary(
            return_1y=0.18,
            return_6m=0.08,
            pct_from_52w_high=-0.03,
            pct_from_52w_low=0.22,
            beta_1y=1.10,
        ),
        fundamentals=FundamentalsTimeseries(
            quarters=[
                QuarterlyFigures(period_end=date(2026, 3, 31), revenue=95_000_000_000, eps_diluted=1.55, fcf=25_000_000_000),
                QuarterlyFigures(period_end=date(2025, 12, 31), revenue=120_000_000_000, eps_diluted=2.10, fcf=35_000_000_000),
                QuarterlyFigures(period_end=date(2025, 9, 30), revenue=89_000_000_000, eps_diluted=1.40, fcf=22_000_000_000),
                QuarterlyFigures(period_end=date(2025, 6, 30), revenue=85_000_000_000, eps_diluted=1.35, fcf=21_000_000_000),
            ],
            revenue_cagr_5y=0.075,
            eps_cagr_5y=0.10,
            fcf_cagr_5y=0.06,
        ),
        run_id=DEFAULT_RUN_ID,
        screening_s3_key=DEFAULT_S3_KEY,
    ),
    "XOM": StockContext(
        symbol="XOM",
        company_name="Exxon Mobil Corporation",
        sector="Energy",
        sub_sector="Integrated Oil & Gas",
        as_of_date=DEFAULT_AS_OF,
        composite_score=1.20,
        momentum_z=0.40,
        value_z=1.20,
        pe_ttm=12.0,
        ev_ebitda=6.5,
        fcf_yield=0.085,
        peer_context=[
            PeerComparable(symbol="CVX", pe_ttm=13.5, ev_ebitda=7.0, fcf_yield=0.075),
            PeerComparable(symbol="COP", pe_ttm=11.0, ev_ebitda=5.8, fcf_yield=0.090),
            PeerComparable(symbol="MPC", pe_ttm=8.5, ev_ebitda=5.2, fcf_yield=0.110),
            PeerComparable(symbol="PSX", pe_ttm=10.0, ev_ebitda=6.0, fcf_yield=0.095),
        ],
        price_summary=PriceSummary(
            return_1y=0.12,
            return_6m=-0.03,
            pct_from_52w_high=-0.08,
            pct_from_52w_low=0.18,
            beta_1y=0.95,
        ),
        fundamentals=FundamentalsTimeseries(
            quarters=[
                QuarterlyFigures(period_end=date(2026, 3, 31), revenue=88_000_000_000, eps_diluted=2.60, fcf=14_000_000_000),
                QuarterlyFigures(period_end=date(2025, 12, 31), revenue=92_000_000_000, eps_diluted=2.85, fcf=16_000_000_000),
                QuarterlyFigures(period_end=date(2025, 9, 30), revenue=87_000_000_000, eps_diluted=2.55, fcf=13_500_000_000),
                QuarterlyFigures(period_end=date(2025, 6, 30), revenue=85_000_000_000, eps_diluted=2.50, fcf=12_500_000_000),
            ],
            revenue_cagr_5y=0.04,
            eps_cagr_5y=0.14,
            fcf_cagr_5y=0.09,
        ),
        run_id=DEFAULT_RUN_ID,
        screening_s3_key=DEFAULT_S3_KEY,
    ),
    "NVDA": StockContext(
        symbol="NVDA",
        company_name="NVIDIA Corporation",
        sector="Technology",
        sub_sector="Semiconductors",
        as_of_date=DEFAULT_AS_OF,
        composite_score=2.10,
        momentum_z=2.50,
        value_z=-1.50,
        pe_ttm=65.0,
        ev_ebitda=50.0,
        fcf_yield=0.015,
        peer_context=[
            PeerComparable(symbol="AMD", pe_ttm=42.0, ev_ebitda=30.0, fcf_yield=0.020),
            PeerComparable(symbol="AVGO", pe_ttm=38.0, ev_ebitda=25.0, fcf_yield=0.030),
            PeerComparable(symbol="QCOM", pe_ttm=22.0, ev_ebitda=15.0, fcf_yield=0.045),
            PeerComparable(symbol="INTC", pe_ttm=28.0, ev_ebitda=12.0, fcf_yield=0.025),
        ],
        price_summary=PriceSummary(
            return_1y=0.85,
            return_6m=0.30,
            pct_from_52w_high=-0.05,
            pct_from_52w_low=1.20,
            beta_1y=1.45,
        ),
        fundamentals=FundamentalsTimeseries(
            quarters=[
                QuarterlyFigures(period_end=date(2026, 3, 31), revenue=38_000_000_000, eps_diluted=5.20, fcf=20_000_000_000),
                QuarterlyFigures(period_end=date(2025, 12, 31), revenue=35_000_000_000, eps_diluted=4.80, fcf=18_500_000_000),
                QuarterlyFigures(period_end=date(2025, 9, 30), revenue=30_000_000_000, eps_diluted=4.10, fcf=15_500_000_000),
                QuarterlyFigures(period_end=date(2025, 6, 30), revenue=28_000_000_000, eps_diluted=3.80, fcf=14_000_000_000),
            ],
            revenue_cagr_5y=0.45,
            eps_cagr_5y=0.60,
            fcf_cagr_5y=0.50,
        ),
        run_id=DEFAULT_RUN_ID,
        screening_s3_key=DEFAULT_S3_KEY,
    ),
    "JPM": StockContext(
        # 금융 sector — docs §10 sector-specific 팩터 보강 효과 평가 케이스.
        # EV/EBITDA 가 구조적으로 부풀고 (예금이 부채로 잡힘), FCF Yield 는
        # 음수가 될 수 있음 (대출 자산 증가가 CF 차감). LLM 이 P/E·EPS·revenue
        # 추세로 대안 reasoning 을 만들어내는지 골든 케이스로 검증.
        symbol="JPM",
        company_name="JPMorgan Chase & Co.",
        sector="Financials",
        sub_sector="Diversified Banks",
        as_of_date=DEFAULT_AS_OF,
        composite_score=0.95,
        momentum_z=0.70,
        value_z=0.50,
        pe_ttm=13.0,
        ev_ebitda=32.0,  # 구조적 왜곡 — peer 들도 비슷
        fcf_yield=-0.20,  # 음수 — 대출 자산 증가
        peer_context=[
            PeerComparable(symbol="BAC", pe_ttm=12.0, ev_ebitda=28.0, fcf_yield=-0.15),
            PeerComparable(symbol="WFC", pe_ttm=11.5, ev_ebitda=30.0, fcf_yield=-0.18),
            PeerComparable(symbol="C", pe_ttm=10.0, ev_ebitda=35.0, fcf_yield=-0.22),
            PeerComparable(symbol="GS", pe_ttm=14.5, ev_ebitda=20.0, fcf_yield=0.05),
        ],
        price_summary=PriceSummary(
            return_1y=0.20,
            return_6m=0.05,
            pct_from_52w_high=-0.02,
            pct_from_52w_low=0.25,
            beta_1y=1.05,
        ),
        fundamentals=FundamentalsTimeseries(
            quarters=[
                QuarterlyFigures(period_end=date(2026, 3, 31), revenue=42_000_000_000, eps_diluted=4.80, fcf=None),
                QuarterlyFigures(period_end=date(2025, 12, 31), revenue=43_500_000_000, eps_diluted=5.20, fcf=None),
                QuarterlyFigures(period_end=date(2025, 9, 30), revenue=41_000_000_000, eps_diluted=4.60, fcf=None),
                QuarterlyFigures(period_end=date(2025, 6, 30), revenue=40_500_000_000, eps_diluted=4.55, fcf=None),
            ],
            revenue_cagr_5y=0.06,
            eps_cagr_5y=0.10,
            fcf_cagr_5y=None,  # 음수 TTM 누적 — CAGR 무의미
        ),
        run_id=DEFAULT_RUN_ID,
        screening_s3_key=DEFAULT_S3_KEY,
    ),
}

DEFAULT_SYMBOLS = list(GOLDEN_FIXTURES.keys())
DEFAULT_STANCES = ("bull", "bear")
DEFAULT_OUTPUT_DIR = ROOT / "tests" / "golden" / "bullbear"


# ---------- 메인 ----------


def _parse_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    p.add_argument(
        "--symbols",
        type=_parse_csv,
        default=DEFAULT_SYMBOLS,
        help=f"쉼표 구분. 기본: {','.join(DEFAULT_SYMBOLS)}",
    )
    p.add_argument(
        "--stances",
        type=_parse_csv,
        default=list(DEFAULT_STANCES),
        help="bull,bear / bull / bear. 기본: bull,bear",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"기본: {DEFAULT_OUTPUT_DIR.relative_to(ROOT)}",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 호출 없이 user 프롬프트만 stdout 출력",
    )
    return p


def _save_snapshot(
    output_dir: Path,
    symbol: str,
    stance: str,
    result: CallResult,
    user_prompt: str,
    input_hash: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "opinion": result.opinion.model_dump(mode="json"),
        "attempts": [asdict(a) for a in result.attempts],
        "prompt_user": user_prompt,
        "input_hash": input_hash,
    }
    out_path = output_dir / f"{symbol}_{stance}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


def _do_dry_run(symbols: Iterable[str], stances: Iterable[str]) -> int:
    for sym in symbols:
        ctx = GOLDEN_FIXTURES[sym]
        print(f"\n========== {sym} (input_hash={context_input_hash(ctx)[:12]}…) ==========")
        for stance in stances:
            print(f"\n--- {sym} / {stance} ---")
            print(to_prompt_markdown(ctx))
    return 0


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    unknown = [s for s in args.symbols if s not in GOLDEN_FIXTURES]
    if unknown:
        parser.error(f"미정의 종목: {unknown}. 사용 가능: {DEFAULT_SYMBOLS}")
    invalid_stance = [s for s in args.stances if s not in DEFAULT_STANCES]
    if invalid_stance:
        parser.error(f"미정의 stance: {invalid_stance}. 사용 가능: {list(DEFAULT_STANCES)}")

    if args.dry_run:
        return _do_dry_run(args.symbols, args.stances)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY 환경변수 필요.", file=sys.stderr)
        return 2

    try:
        from agents.bull_bear.anthropic_adapter import AnthropicSDKCaller
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    caller = AnthropicSDKCaller(api_key=api_key)
    total_cost = 0.0
    total_attempts = 0

    for sym in args.symbols:
        ctx = GOLDEN_FIXTURES[sym]
        input_hash = context_input_hash(ctx)
        for stance in args.stances:
            print(f"[{sym}/{stance}] 호출 시작 (input_hash={input_hash[:12]}…)")
            try:
                result = run_bullbear_agent(ctx, stance, caller=caller)  # type: ignore[arg-type]
            except BullBearAgentError as err:
                print(f"  실패 — {err}. 시도: {len(err.attempts)}회", file=sys.stderr)
                for a in err.attempts:
                    print(f"    {a.stage}/{a.model}: {a.error}", file=sys.stderr)
                continue

            from agents.bull_bear.agent import _user_prompt  # noqa: PLC0415 — 진단용

            user_prompt = _user_prompt(ctx, stance)  # type: ignore[arg-type]
            out_path = _save_snapshot(args.output_dir, sym, stance, result, user_prompt, input_hash)
            print(
                f"  → {out_path.relative_to(ROOT)} "
                f"(attempts={len(result.attempts)}, cost=${result.total_cost_usd:.4f})"
            )
            total_cost += result.total_cost_usd
            total_attempts += len(result.attempts)

    print(f"\n총 시도: {total_attempts}회, 총 비용: ${total_cost:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
