"""4단계 optimizer 로컬 dry-run (04 §11 #6) — S3 읽기 실데이터, 쓰기는 로컬.

사용:
  .venv/bin/python scripts/run_optimizer_dry.py [--dt 2026-08-10] [--bucket ...]

출력: retro_data/optimizer_dry/dt={dt}/target.json + 콘솔 요약.
LLM 0 / S3 쓰기 0 (portfolios/ 는 컨테이너 배포 후 운영 경로에서만).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from optimizer import lambda_core  # noqa: E402
from optimizer.schemas import OptimizerBundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dt", default=None)
    parser.add_argument("--bucket", default=os.environ.get("S3_BUCKET", "portfolio-mvp-data-s3"))
    args = parser.parse_args()
    os.environ["S3_BUCKET"] = args.bucket

    captured: dict[str, str] = {}
    lambda_core.write_text = lambda b, k, t, **kw: captured.update({k: t})  # type: ignore[assignment]

    out = lambda_core.handle({"dt": args.dt} if args.dt else {}, None)

    (key, body), = captured.items()
    local = ROOT / "retro_data" / "optimizer_dry" / Path(key).parent.name / "target.json"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(body)

    bundle = OptimizerBundle.model_validate_json(body)
    for name, tp in (("PRIMARY (옵션 C)", bundle.primary),
                     ("BASELINE (옵션 B)", bundle.option_b_baseline)):
        if tp is None:
            continue
        print(f"\n===== {name} — dt={tp.as_of_date} =====")
        print(f"universe {tp.universe_size} / 후보 {tp.n_candidates} / "
              f"종목 {len(tp.weights)} / 현금 {tp.cash_weight:.0%} / "
              f"ER {tp.expected_portfolio_return:.2%} / σ {tp.portfolio_variance ** 0.5:.2%}")
        for s, w in sorted(tp.weights.items(), key=lambda kv: -kv[1]):
            print(f"  {s:5} {w:6.1%}")
        if tp.excluded:
            print(f"  제외: {tp.excluded}")
    print(f"\n로컬 저장: {local}  (S3 쓰기 없음 — dry-run)")
    print(json.dumps({"summary": {k: v for k, v in out.items() if k != 'excluded'}},
                     ensure_ascii=False, default=str)[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
