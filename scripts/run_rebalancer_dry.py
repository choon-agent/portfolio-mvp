"""5단계 rebalancer 로컬 dry-run + 리플레이 (05 §10 #5·#7).

사용:
  # 최신 1주 dry-run (S3 읽기 실데이터, 쓰기는 로컬 캡처)
  .venv/bin/python scripts/run_rebalancer_dry.py [--dt 2026-09-07]

  # 백필 리플레이 (§4.1): portfolios/ 에 존재하는 dt 를 순서대로 체이닝
  .venv/bin/python scripts/run_rebalancer_dry.py --replay-from 2026-08-17

  # 백필 S3 박제 (구현 #7 — 명시적 --upload 없이는 S3 쓰기 없음)
  .venv/bin/python scripts/run_rebalancer_dry.py --replay-from 2026-08-17 --upload

--force: 멱등 가드(§4.3) 해제 — 기존 snapshot 이 있는 dt 도 재계산 (오염 주의,
로컬 캡처 모드에서는 안전). 과거 as_of 재실행 금지 교훈은 rebalancer 에는
해당 없음 (입력이 전부 dt 파티션 고정 — 재계산 비결정성 없음).

LLM 0. 출력: retro_data/rebalancer_dry/ + 콘솔 주차별 요약.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import boto3  # noqa: E402

from rebalancer import lambda_core  # noqa: E402


def _portfolio_dts(bucket: str) -> list[str]:
    s3 = boto3.client("s3")
    dts: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="portfolios/dt=", Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            dts.add(cp["Prefix"].split("dt=")[1].rstrip("/"))
    return sorted(dts)


def _print_week(out: dict) -> None:
    for a in out["accounts"]:
        if a["status"] == "skipped_existing_snapshot":
            print(f"  {a['account_id']:9} skip (snapshot 존재)")
            continue
        ret = a["weekly_return"]
        spy = a["spy_weekly_return"]
        print(
            f"  {a['account_id']:9} {a['status']:10} nav ${a['nav']:>9,.2f} "
            f"현금 ${a['cash']:>8,.2f} 종목 {a['n_positions']:2} "
            f"매매 {a['n_trades']:2} (band skip {a['n_skipped_by_band']}) "
            f"r={'—' if ret is None else f'{ret:+.2%}'} "
            f"spy={'—' if spy is None else f'{spy:+.2%}'}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dt", default=None)
    parser.add_argument("--replay-from", default=None, metavar="YYYY-MM-DD")
    parser.add_argument("--upload", action="store_true",
                        help="S3 에 실제 기록 (백필 박제 — 구현 #7)")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--bucket",
                        default=os.environ.get("S3_BUCKET", "portfolio-mvp-data-s3"))
    args = parser.parse_args()
    os.environ["S3_BUCKET"] = args.bucket

    captured: dict[str, str] = {}
    if not args.upload:
        # 쓰기 로컬 캡처 + 읽기는 캡처 우선 (리플레이 상태 체이닝)
        real_read, real_exists = lambda_core.read_json, lambda_core.object_exists
        lambda_core.write_text = (  # type: ignore[assignment]
            lambda b, k, t, **kw: captured.update({k: t})
        )
        lambda_core.read_json = (  # type: ignore[assignment]
            lambda b, k: json.loads(captured[k]) if k in captured else real_read(b, k)
        )
        lambda_core.object_exists = (  # type: ignore[assignment]
            lambda b, k: k in captured or real_exists(b, k)
        )

    if args.replay_from:
        dts = [d for d in _portfolio_dts(args.bucket) if d >= args.replay_from]
        if not dts:
            print(f"portfolios/ 에 {args.replay_from} 이후 파티션 없음")
            return 1
    else:
        dts = [args.dt] if args.dt else [None]

    for dt in dts:
        event: dict = {"force": args.force}
        if dt:
            event["dt"] = dt
        out = lambda_core.handle(event, None)
        print(f"\n===== dt={out['dt']} =====")
        _print_week(out)

    if captured:
        base = ROOT / "retro_data" / "rebalancer_dry"
        for key, body in captured.items():
            local = base / key
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text(body)
        print(f"\n로컬 저장 {len(captured)}건: {base}  (S3 쓰기 없음 — dry-run)")
    elif args.upload:
        print("\nS3 기록 완료 (--upload)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
