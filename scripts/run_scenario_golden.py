"""시나리오 골든 스냅샷 생성 — 실제 LLM 호출 (M3 #8).

설계 근거: docs/03-scenario.md §10.3, §11 #8

4종목(AAPL/XOM/NVDA/JPM)에 대해:
1. Bull/Bear 골든 스냅샷(tests/golden/bullbear/{sym}_{stance}.json)을 입력 의견으로 로드
2. 고정 가격 컨텍스트 fixture 와 합쳐 ScenarioContext 조립
3. run_scenario_agent 로 실제 LLM 호출 → ScenarioOpinion
4. compute_expected_return → ExpectedReturn
5. tests/golden/scenario/{sym}.json 으로 스냅샷 저장

비용: 4 × ~$0.018 ≈ $0.072 (1회). CI 반복 아님 — `pytest -m golden` 은 저장된
스냅샷 replay 검증만 (LLM 호출 0, test_scenario_golden.py).

사용:
  ANTHROPIC_API_KEY=sk-... .venv/bin/python scripts/run_scenario_golden.py
  옵션:
    --symbols AAPL,XOM        # 기본 4종목
    --dry-run                 # 실제 호출 없이 프롬프트만 인쇄
    --output-dir DIR          # 기본 tests/golden/scenario

가격 컨텍스트를 고정 fixture 로 두는 이유 (Bull/Bear 골든과 동일): 스냅샷은
결정적 입력이어야 회귀 비교가 의미 있다. 운영 OHLCV/펀더멘털은 시점마다 변해
LLM 응답 변동이 입력 변동인지 모델 변동인지 분리 불가.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

# scripts/ 에서 src/ import 가능하도록 path 보강
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.bull_bear.schemas import BullBearOpinion  # noqa: E402
from agents.scenario.agent import (  # noqa: E402
    ScenarioAgentError,
    _user_prompt,
    run_scenario_agent,
    scenario_input_hash,
)
from agents.scenario.context_builder import to_prompt_markdown  # noqa: E402
from agents.scenario.pricing import compute_expected_return  # noqa: E402
from agents.scenario.pricing_config import ScenarioPricingConfig  # noqa: E402
from agents.scenario.schemas import ScenarioContext  # noqa: E402

DEFAULT_AS_OF = date(2026, 4, 27)
DEFAULT_RUN_ID = "golden-2026-04-27"
BULLBEAR_DIR = ROOT / "tests" / "golden" / "bullbear"
DEFAULT_OUTPUT_DIR = ROOT / "tests" / "golden" / "scenario"
DEFAULT_SYMBOLS = ["AAPL", "XOM", "NVDA", "JPM"]

# ---------- Fixture: 종목별 가격 컨텍스트 (고정, 2026-04-27 기준 plausible) ----------

GOLDEN_PRICE_FIXTURES: dict[str, dict[str, Any]] = {
    "AAPL": {
        "company_name": "Apple Inc.",
        "sector": "Technology",
        "sub_sector": "Consumer Electronics",
        "current_price": 190.0,
        "ttm_eps": 6.5,
        "peer_pe": [28.0, 30.0, 25.0],
        "return_52w_high": 0.08,
        "return_52w_low": -0.25,
    },
    "XOM": {
        "company_name": "Exxon Mobil Corporation",
        "sector": "Energy",
        "sub_sector": "Oil & Gas Integrated",
        "current_price": 110.0,
        "ttm_eps": 8.0,
        "peer_pe": [12.0, 14.0, 11.0],
        "return_52w_high": 0.15,
        "return_52w_low": -0.18,
    },
    "NVDA": {
        "company_name": "NVIDIA Corporation",
        "sector": "Technology",
        "sub_sector": "Semiconductors",
        "current_price": 110.0,
        "ttm_eps": 2.8,
        "peer_pe": [40.0, 35.0, 45.0],
        "return_52w_high": 0.20,
        "return_52w_low": -0.40,
    },
    "JPM": {
        "company_name": "JPMorgan Chase & Co.",
        "sector": "Financials",
        "sub_sector": "Banks",
        "current_price": 205.0,
        "ttm_eps": 17.0,
        "peer_pe": [12.0, 11.0, 13.0],
        "return_52w_high": 0.10,
        "return_52w_low": -0.20,
    },
}


def _load_opinion(symbol: str, stance: str) -> BullBearOpinion:
    """Bull/Bear 골든 스냅샷에서 의견 로드."""
    path = BULLBEAR_DIR / f"{symbol}_{stance}.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"Bull/Bear 골든 스냅샷 없음: {path.relative_to(ROOT)} "
            f"(먼저 scripts/run_bullbear_golden.py 실행 필요)"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return BullBearOpinion.model_validate(payload["opinion"])


def _build_context(symbol: str) -> ScenarioContext:
    fx = GOLDEN_PRICE_FIXTURES[symbol]
    return ScenarioContext(
        symbol=symbol,
        company_name=fx["company_name"],
        sector=fx["sector"],
        sub_sector=fx["sub_sector"],
        as_of_date=DEFAULT_AS_OF,
        bull_opinion=_load_opinion(symbol, "bull"),
        bear_opinion=_load_opinion(symbol, "bear"),
        current_price=fx["current_price"],
        ttm_eps=fx["ttm_eps"],
        peer_pe=fx["peer_pe"],
        return_52w_high=fx["return_52w_high"],
        return_52w_low=fx["return_52w_low"],
        run_id=DEFAULT_RUN_ID,
        scenario_s3_key=f"scenarios/dt={DEFAULT_AS_OF.isoformat()}/symbol={symbol}.json",
        bullbear_s3_keys={
            "bull": f"agents/bullbear/dt={DEFAULT_AS_OF.isoformat()}/symbol={symbol}/stance=bull.json",
            "bear": f"agents/bullbear/dt={DEFAULT_AS_OF.isoformat()}/symbol={symbol}/stance=bear.json",
        },
    )


# ---------- CLI ----------


def _parse_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="시나리오 골든 스냅샷 생성 (실제 LLM 호출)")
    p.add_argument("--symbols", type=_parse_csv, default=DEFAULT_SYMBOLS)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--dry-run", action="store_true", help="실제 호출 없이 프롬프트만 인쇄")
    return p


def _save_snapshot(output_dir: Path, symbol: str, ctx: ScenarioContext, result: Any, expected_return: Any, user_prompt: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "scenario_opinion": result.opinion.model_dump(mode="json"),
        "expected_return": expected_return.model_dump(mode="json"),
        "scenario_context": ctx.model_dump(mode="json"),
        "attempts": [asdict(a) for a in result.attempts],
        "prompt_user": user_prompt,
        "input_hash": scenario_input_hash(ctx),
    }
    out_path = output_dir / f"{symbol}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


def _do_dry_run(symbols: Iterable[str]) -> int:
    for sym in symbols:
        ctx = _build_context(sym)
        print(f"\n========== {sym} (input_hash={scenario_input_hash(ctx)[:12]}…) ==========")
        print(to_prompt_markdown(ctx))
    return 0


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    unknown = [s for s in args.symbols if s not in GOLDEN_PRICE_FIXTURES]
    if unknown:
        parser.error(f"미정의 종목: {unknown}. 사용 가능: {DEFAULT_SYMBOLS}")

    if args.dry_run:
        return _do_dry_run(args.symbols)

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
    cfg = ScenarioPricingConfig()
    total_cost = 0.0
    total_attempts = 0

    for sym in args.symbols:
        ctx = _build_context(sym)
        print(f"[{sym}] 호출 시작 (input_hash={scenario_input_hash(ctx)[:12]}…)")
        try:
            result = run_scenario_agent(ctx, caller=caller)
        except ScenarioAgentError as err:
            print(f"  실패 — {err}. 시도: {len(err.attempts)}회", file=sys.stderr)
            for a in err.attempts:
                print(f"    {a.stage}/{a.model}: {a.error}", file=sys.stderr)
            continue

        expected_return = compute_expected_return(result.opinion, ctx, cfg)
        user_prompt = _user_prompt(ctx)
        out_path = _save_snapshot(args.output_dir, sym, ctx, result, expected_return, user_prompt)
        probs = [round(s.probability, 2) for s in result.opinion.scenarios]
        print(
            f"  → {out_path.relative_to(ROOT)} "
            f"(attempts={len(result.attempts)}, cost=${result.total_cost_usd:.4f}, "
            f"probs={probs}, exp_return={expected_return.expected_return:+.3f})"
        )
        total_cost += result.total_cost_usd
        total_attempts += len(result.attempts)

    print(f"\n총 시도: {total_attempts}회, 총 비용: ${total_cost:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
