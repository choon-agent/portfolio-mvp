"""3단계 확률의 재호출 분산 측정 — temperature 미전송(Sonnet 5) 환경 (retro §0.8).

질문: 같은 ScenarioContext 를 N회 다시 물으면 bull/base/bear 확률이 얼마나 흔들리고,
그 흔들림이 ER 부호(= 4단계 후보 여부)를 뒤집는가? LLM 이 매매 경로에 주는 유일한
확률 입력이 3단계 확률이므로, 이 실험이 "결정성 부재" 우려의 크기를 정량화한다.

- 입력: S3 `scenario_contexts/dt=D/symbol=X.json` (Bull/Bear 의견 포함 — 완전 고정)
- 호출: `run_scenario_agent` × N (운영과 동일 어댑터·프롬프트). 캐시 없음 (직접 호출)
- 산출: 표본 = 저장 운영값 1 + 재호출 N. 확률 sd/range, ER range, 부호 반전 횟수,
  bear=base=현재가 퇴화 종목은 ER = p_bull × (bull/현재가 − 1) 이므로 p_bull 분산만 영향
- 종목 선정 (`--symbols auto`): flag 없는 종목 중 |ER| 최소 2 (경계) + ER 최대 1 + 최소 1

사용:
  ANTHROPIC_API_KEY=... .venv/bin/python scripts/run_probability_variance.py \
      [--dt 2026-09-21] [--symbols auto|MPC,APA] [--runs 4] [--output-dir retro_data/prob_variance]
비용: 종목 × runs × ~$0.015 (Sonnet 5). 기본 4×4 ≈ $0.25. S3 쓰기 0.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.scenario.agent import run_scenario_agent  # noqa: E402
from agents.scenario.pricing import compute_expected_return  # noqa: E402
from agents.scenario.pricing_config import ScenarioPricingConfig  # noqa: E402
from agents.scenario.schemas import ScenarioContext, ScenarioOpinion  # noqa: E402
from common.s3_io import read_json  # noqa: E402

CFG = ScenarioPricingConfig()


def _probs(op: ScenarioOpinion) -> dict[str, float]:
    return {s.label: s.probability for s in op.scenarios}


def _select_auto(bucket: str, dt: str) -> list[str]:
    screening = read_json(bucket, f"screening/dt={dt}/result.json") or {}
    rows = []
    for s in [x["symbol"] for x in screening.get("selected", [])]:
        er = read_json(bucket, f"expected_returns/dt={dt}/symbol={s}.json")
        if er is None:
            continue
        p = er["primary"]
        if p["data_quality_flags"]:
            continue
        rows.append((s, p["expected_return"]))
    by_abs = sorted(rows, key=lambda r: abs(r[1]))
    picks = [by_abs[0][0], by_abs[1][0], max(rows, key=lambda r: r[1])[0], min(rows, key=lambda r: r[1])[0]]
    return list(dict.fromkeys(picks))  # 중복 제거·순서 유지


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", default=os.environ.get("S3_BUCKET", "portfolio-mvp-data-s3"))
    ap.add_argument("--dt", default="2026-09-21")
    ap.add_argument("--symbols", default="auto")
    ap.add_argument("--runs", type=int, default=4)
    ap.add_argument("--output-dir", default="retro_data/prob_variance")
    args = ap.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY 환경변수 필요.", file=sys.stderr)
        return 2
    from agents.bull_bear.anthropic_adapter import AnthropicSDKCaller
    caller = AnthropicSDKCaller(api_key=api_key)

    symbols = _select_auto(args.bucket, args.dt) if args.symbols == "auto" else args.symbols.split(",")
    print(f"dt={args.dt} symbols={symbols} runs={args.runs}")

    report: dict = {"dt": args.dt, "runs": args.runs, "generated_at": datetime.now(timezone.utc).isoformat(), "symbols": {}}
    total_cost = 0.0
    for sym in symbols:
        ctx = ScenarioContext.model_validate(read_json(args.bucket, f"scenario_contexts/dt={args.dt}/symbol={sym}.json"))
        stored = ScenarioOpinion.model_validate(
            read_json(args.bucket, f"scenarios/dt={args.dt}/symbol={sym}.json")["scenario_opinion"]
        )
        samples = [{"src": "stored", "probs": _probs(stored),
                    "er": compute_expected_return(stored, ctx, CFG).expected_return}]
        for i in range(args.runs):
            res = run_scenario_agent(ctx, caller=caller)
            total_cost += res.total_cost_usd
            er = compute_expected_return(res.opinion, ctx, CFG)
            samples.append({"src": f"run{i + 1}", "probs": _probs(res.opinion),
                            "er": er.expected_return, "attempts": len(res.attempts)})
        pb = [s["probs"]["bull"] for s in samples]
        pr = [s["probs"]["bear"] for s in samples]
        ers = [s["er"] for s in samples]
        base_er = compute_expected_return(stored, ctx, CFG)
        sp = base_er.scenario_prices
        cp = ctx.current_price
        degen = abs(sp["bear"] - cp) < 1e-6 and abs(sp["base"] - cp) < 1e-6
        signs = {e > 0 for e in ers}
        rec = {
            "current_price": cp, "return_52w_high": ctx.return_52w_high, "degenerate": degen,
            "p_bull": {"min": min(pb), "max": max(pb), "sd": statistics.pstdev(pb), "values": pb},
            "p_bear": {"min": min(pr), "max": max(pr), "sd": statistics.pstdev(pr), "values": pr},
            "er": {"stored": ers[0], "min": min(ers), "max": max(ers), "range_pp": (max(ers) - min(ers)) * 100},
            "candidate_flip": len(signs) > 1,
            "retries": sum(s.get("attempts", 1) > 1 for s in samples[1:]),
            "samples": samples,
        }
        report["symbols"][sym] = rec
        print(f"\n{sym:5} px {cp:.2f} 52wH {ctx.return_52w_high:+.3f} {'DEGEN' if degen else ''}")
        print(f"  p_bull {pb}  sd {rec['p_bull']['sd']:.3f}")
        print(f"  p_bear {pr}  sd {rec['p_bear']['sd']:.3f}")
        print(f"  ER%    {[round(e * 100, 2) for e in ers]}  range {rec['er']['range_pp']:.2f}%p"
              f"  {'** 후보 부호 반전 **' if rec['candidate_flip'] else '부호 안정'}  retries {rec['retries']}")

    report["total_cost_usd"] = total_cost
    out = ROOT / args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{args.dt}_runs{args.runs}.json"
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"\n총 호출 {len(symbols) * args.runs}회, 비용 ${total_cost:.3f} → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
