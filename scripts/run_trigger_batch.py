"""트리거 자동 검증 로컬 배치 — M3 #13 PoC (분기 발표 후 tripwire 채점).

설계 근거: docs/03-scenario.md §7, §12.2 D / §12.3 B·F

S3 에 저장된 과거 ScenarioOpinion 들에 대해:
1. 시간 조인 (§12.3 B): 분기 income statement 중 `filingDate > 시나리오 as_of_date`
   인 *가장 이른* 발표를 "다음 분기 발표" 로 매칭. 아직 발표 전이면 스킵
   (tripwire = 시나리오당 1회 평가, §7.1)
2. 신선도 (§12.3 F): `--max-cache-age-days` (기본 3) 로 90일 TTL 을 우회해
   발표 직후 statement 를 캐시 계층 경유로 재수집 (캐시 갱신 부수효과는 의도된
   동작 — 이후 주간 실행이 새 분기 데이터를 보게 됨)
3. 트리거 채점: scenario 3개(bull/base/bear)의 invalidation_trigger 를
   `evaluate_trigger` 로 자동 평가 (guidance_change/peer_announcement 는
   requires_human_review 로 표시만)
4. calibration: 발표일 이후 첫 거래일 종가를 앵커로 `realized_scenario` 분류 +
   `brier_score`. 가격 순서 위반(data_quality_flags) 종목은 §7.2 에 따라
   calibration 표본에서 제외 (트리거 채점은 수행)

observe-only (§7.2 E): 결과는 회고 데이터 누적 전용 — 매매·재분석·리밸런싱에
피드백되지 않음.

v1 한계: FMPClient 에 balance-sheet / earnings-surprises 엔드포인트가 없어
`net_debt_yoy` / `earnings_surprise` metric 은 met=None (평가 불가) 처리.
사용 빈도가 유의미하면 클라이언트 확장 후 재평가 (§12.3).

사용:
  FMP_API_KEY=... .venv/bin/python scripts/run_trigger_batch.py
  옵션:
    --bucket portfolio-mvp-data-s3   # 기본 $S3_BUCKET 또는 이 값
    --fmp-secret-id NAME             # FMP_API_KEY 대신 Secrets Manager 사용
    --dts 2026-05-31,2026-06-01      # 기본: scenarios/ 전체 dt 스캔
    --symbols CNC,MU                 # 기본: 각 dt 의 전 종목
    --max-cache-age-days 3           # statement 캐시 신선도 (F)
    --output-dir retro_data/trigger_evaluations
    --upload                         # S3 trigger_evaluations/ 에도 저장
    --force                          # --upload 시 기존 평가 덮어씀 (tripwire 재실행)

비용: LLM 0. FMP 호출 ~2/종목 (income+cashflow, 캐시 stale 시에만).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3

# scripts/ 에서 src/ import 가능하도록 path 보강
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.scenario.schemas import ScenarioContext, ScenarioOpinion  # noqa: E402
from agents.scenario.schemas import ExpectedReturn, ExpectedReturnsBundle  # noqa: E402
from agents.scenario.trigger_evaluator import (  # noqa: E402
    brier_score,
    evaluate_trigger,
    realized_scenario,
)
from common.fmp_client import FMPClient  # noqa: E402
from common.fundamentals import (  # noqa: E402
    fetch_cashflow_quarterly_with_cache,
    fetch_income_quarterly_with_cache,
)
from common.s3_io import get_secret, read_json, read_parquet, write_json  # noqa: E402

SCENARIOS_PREFIX = "scenarios"
EXPECTED_RETURNS_PREFIX = "expected_returns"
CONTEXTS_PREFIX = "scenario_contexts"
OHLCV_PREFIX = "ohlcv"
OUTPUT_PREFIX = "trigger_evaluations"

_s3 = boto3.client("s3")


# ---------- S3 스캔 ----------


def list_scenario_keys(bucket: str) -> dict[str, list[str]]:
    """scenarios/dt=.../symbol=....json 전체 스캔 → {dt: [symbol, ...]}."""
    out: dict[str, list[str]] = {}
    paginator = _s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{SCENARIOS_PREFIX}/dt="):
        for obj in page.get("Contents", []):
            key = obj["Key"]  # scenarios/dt=2026-05-31/symbol=CNC.json
            parts = key.split("/")
            if len(parts) != 3 or not parts[2].endswith(".json"):
                continue
            dt = parts[1].removeprefix("dt=")
            sym = parts[2].removeprefix("symbol=").removesuffix(".json")
            out.setdefault(dt, []).append(sym)
    return out


# ---------- 시간 조인 (§12.3 B) ----------


def _filing_date(row: dict[str, Any]) -> str | None:
    """행의 발표일 — stable API `filingDate` 우선, 없으면 acceptedDate 의 날짜부."""
    fd = row.get("filingDate")
    if isinstance(fd, str) and len(fd) >= 10:
        return fd[:10]
    ad = row.get("acceptedDate")
    if isinstance(ad, str) and len(ad) >= 10:
        return ad[:10]
    return None


def match_next_filing(
    income: list[dict[str, Any]], scenario_dt: str
) -> tuple[dict[str, Any], str] | None:
    """as_of_date 이후 *가장 이른* 분기 발표 행과 그 발표일. 미발표면 None."""
    candidates: list[tuple[str, dict[str, Any]]] = []
    for row in income:
        fd = _filing_date(row)
        if fd is not None and fd > scenario_dt:
            candidates.append((fd, row))
    if not candidates:
        return None
    fd, row = min(candidates, key=lambda x: x[0])
    return row, fd


def rows_up_to(rows: list[dict[str, Any]], period_end: str) -> list[dict[str, Any]]:
    """매칭된 분기(period_end)가 최신 행(inc[0])이 되도록 이후 분기 행 제거.

    evaluate_trigger 는 rows[0] 을 '발표 분기' 로 가정 — 오래된 시나리오를
    나중에 평가할 때 그 사이 추가 발표가 섞이면 잘못된 분기를 채점하게 됨.
    """
    return [r for r in rows if isinstance(r.get("date"), str) and r["date"] <= period_end]


# ---------- 가격 앵커 (§7.2 — 발표일 이후 첫 거래일 종가) ----------


def anchor_close(
    bucket: str, symbol: str, filing_date: str
) -> tuple[str, float] | None:
    table = read_parquet(bucket, f"{OHLCV_PREFIX}/ticker={symbol}/data.parquet")
    if table is None:
        return None
    dates = table.column("date").to_pylist()
    closes = table.column("close").to_pylist()
    after = [
        (d, c)
        for d, c in zip(dates, closes)
        if str(d) >= filing_date and c is not None
    ]
    if not after:
        return None  # OHLCV 가 발표일까지 아직 안 닿음 (스킵 후 다음 실행에서 채점)
    d, c = min(after, key=lambda x: str(x[0]))
    return str(d), float(c)


# ---------- 단일 (dt, symbol) 평가 ----------


def evaluate_one(
    *,
    bucket: str,
    dt: str,
    symbol: str,
    fmp: FMPClient,
    max_cache_age_days: int,
    now: datetime,
) -> dict[str, Any]:
    scenario_key = f"{SCENARIOS_PREFIX}/dt={dt}/symbol={symbol}.json"
    saved = read_json(bucket, scenario_key)
    if saved is None:
        return {"symbol": symbol, "scenario_dt": dt, "status": "scenario_missing"}
    opinion = ScenarioOpinion.model_validate(saved["scenario_opinion"])

    bundle_raw = read_json(
        bucket, f"{EXPECTED_RETURNS_PREFIX}/dt={dt}/symbol={symbol}.json"
    )
    ctx_raw = read_json(bucket, f"{CONTEXTS_PREFIX}/dt={dt}/symbol={symbol}.json")
    sub_sector = None
    if ctx_raw is not None:
        sub_sector = ScenarioContext.model_validate(ctx_raw).sub_sector

    income = fetch_income_quarterly_with_cache(
        fmp, bucket, symbol, max_age_days=max_cache_age_days
    )
    matched = match_next_filing(income, dt)
    if matched is None:
        return {"symbol": symbol, "scenario_dt": dt, "status": "not_filed_yet"}
    filing_row, filing_date = matched
    period_end = filing_row["date"]

    cashflow = fetch_cashflow_quarterly_with_cache(
        fmp, bucket, symbol, max_age_days=max_cache_age_days
    )
    income_upto = rows_up_to(income, period_end)
    cashflow_upto = rows_up_to(cashflow, period_end)

    evaluations = [
        evaluate_trigger(
            sc.invalidation_trigger,
            symbol=symbol,
            scenario_label=sc.label,
            scenario_s3_key=scenario_key,
            evaluated_at=now,
            income_quarterly=income_upto,
            cashflow_quarterly=cashflow_upto,
            balance_quarterly=None,   # v1 한계 — FMPClient 에 BS 엔드포인트 없음
            earnings_surprises=None,  # v1 한계 — earnings-surprises 미지원
            sub_sector=sub_sector,
        ).model_dump(mode="json")
        for sc in opinion.scenarios
    ]

    # ---------- calibration (realized + Brier) ----------
    calibration: dict[str, Any] = {"included": False}
    if bundle_raw is None:
        calibration["excluded_reason"] = "expected_returns_missing"
    else:
        # v0.15 이전(#12 sensitivity 도입 전) 주차는 Bundle 이 아닌 단일
        # ExpectedReturn 포맷 — 두 형식 모두 수용
        if "primary" in bundle_raw:
            primary = ExpectedReturnsBundle.model_validate(bundle_raw).primary
        else:
            primary = ExpectedReturn.model_validate(bundle_raw)
        if primary.data_quality_flags:
            # §7.2 — 가격 순서 위반은 bin 경계 신뢰 불가, 표본 제외
            calibration["excluded_reason"] = "data_quality_flags"
            calibration["flags"] = primary.data_quality_flags
        else:
            anchor = anchor_close(bucket, symbol, filing_date)
            if anchor is None:
                calibration["excluded_reason"] = "ohlcv_not_covering_filing_date"
            else:
                anchor_date, close = anchor
                realized = realized_scenario(close, dict(primary.scenario_prices))
                probs = {s.label: s.probability for s in opinion.scenarios}
                calibration = {
                    "included": True,
                    "anchor_date": anchor_date,
                    "anchor_close": close,
                    "scenario_prices": dict(primary.scenario_prices),
                    "probabilities": probs,
                    "realized": realized,
                    "brier": brier_score(probs, realized),
                }

    return {
        "symbol": symbol,
        "scenario_dt": dt,
        "status": "evaluated",
        "evaluated_at": now.isoformat(),
        "filing_date": filing_date,
        "period_end": period_end,
        "sub_sector": sub_sector,
        "trigger_evaluations": evaluations,
        "calibration": calibration,
    }


# ---------- 요약 출력 ----------


def print_summary(records: list[dict[str, Any]]) -> None:
    status = Counter(r["status"] for r in records)
    print("\n========== 배치 요약 ==========")
    print(f"대상 (dt, symbol): {len(records)}  상태: {dict(status)}")

    evaluated = [r for r in records if r["status"] == "evaluated"]
    if not evaluated:
        return

    met_rows, human_rows, none_metrics = [], [], Counter()
    for r in evaluated:
        for ev in r["trigger_evaluations"]:
            tag = f"{r['scenario_dt']} {r['symbol']}/{ev['scenario_label']}:{ev['metric']}"
            if ev["requires_human_review"]:
                human_rows.append(tag)
            elif ev["met"] is True:
                met_rows.append(f"{tag} (actual={ev['actual']}, thr={ev['threshold']})")
            elif ev["met"] is None:
                none_metrics[ev["metric"]] += 1

    n_triggers = sum(len(r["trigger_evaluations"]) for r in evaluated)
    print(f"\n트리거 채점: {n_triggers}건 — 발동(met) {len(met_rows)} / "
          f"인간검토 {len(human_rows)} / 평가불가 {sum(none_metrics.values())} {dict(none_metrics)}")
    for row in met_rows:
        print(f"  [발동] {row}")
    if human_rows:
        print(f"  [인간 검토 대기] {len(human_rows)}건: " + ", ".join(human_rows[:10])
              + (" ..." if len(human_rows) > 10 else ""))

    cal = [r["calibration"] for r in evaluated if r["calibration"].get("included")]
    excluded = Counter(
        r["calibration"].get("excluded_reason")
        for r in evaluated if not r["calibration"].get("included")
    )
    print(f"\ncalibration 표본: {len(cal)} (제외 {dict(excluded)})")
    if cal:
        briers = [c["brier"] for c in cal]
        realized_counts = Counter(c["realized"] for c in cal)
        print(f"  realized 분포: {dict(realized_counts)}")
        print(f"  Brier: 평균 {sum(briers)/len(briers):.4f} / "
              f"min {min(briers):.4f} / max {max(briers):.4f} "
              f"(uniform≈0.667, 합격선 <0.25 — §1.4.2 는 12주 누적 기준)")


# ---------- main ----------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--bucket", default=os.environ.get("S3_BUCKET", "portfolio-mvp-data-s3"))
    parser.add_argument("--fmp-secret-id", default=os.environ.get("FMP_SECRET_ID"))
    parser.add_argument("--dts", help="콤마 구분 dt 목록 (기본: 전체)")
    # 2026-08-10 은 08-17 사고로 오염된 파티션 (retro §0.5) — raw 가 08-17
    # 재계산본이라 tripwire·calibration 모두 lineage 불일치 → 기본 제외
    parser.add_argument("--skip-dts", default="2026-08-10",
                        help="채점 제외 dt 목록 (콤마 — 기본: 오염 파티션 2026-08-10)")
    parser.add_argument("--symbols", help="콤마 구분 심볼 (기본: 각 dt 전체)")
    parser.add_argument("--max-cache-age-days", type=int, default=3)
    parser.add_argument("--output-dir", default=str(ROOT / "retro_data" / "trigger_evaluations"))
    parser.add_argument("--upload", action="store_true", help="S3 trigger_evaluations/ 에도 저장")
    parser.add_argument("--force", action="store_true", help="--upload 시 기존 평가 덮어씀")
    args = parser.parse_args()

    api_key = os.environ.get("FMP_API_KEY")
    if not api_key and args.fmp_secret_id:
        api_key = get_secret(args.fmp_secret_id)
    if not api_key:
        print("ERROR: FMP_API_KEY 또는 --fmp-secret-id 필요", file=sys.stderr)
        return 1
    fmp = FMPClient(api_key=api_key)

    scan = list_scenario_keys(args.bucket)
    skip = set(args.skip_dts.split(",")) if args.skip_dts else set()
    dts = sorted(
        d for d in (args.dts.split(",") if args.dts else scan.keys())
        if d not in skip
    )
    if skip:
        print(f"제외 dt (오염 파티션 등): {sorted(skip)}")
    only_symbols = set(args.symbols.split(",")) if args.symbols else None
    now = datetime.now(timezone.utc)
    out_dir = Path(args.output_dir)

    records: list[dict[str, Any]] = []
    for dt in dts:
        for sym in sorted(scan.get(dt, [])):
            if only_symbols and sym not in only_symbols:
                continue
            s3_out_key = f"{OUTPUT_PREFIX}/dt={dt}/symbol={sym}.json"
            if args.upload and not args.force and read_json(args.bucket, s3_out_key) is not None:
                records.append({"symbol": sym, "scenario_dt": dt, "status": "already_evaluated"})
                continue
            rec = evaluate_one(
                bucket=args.bucket, dt=dt, symbol=sym, fmp=fmp,
                max_cache_age_days=args.max_cache_age_days, now=now,
            )
            records.append(rec)
            print(f"{dt} {sym:6} → {rec['status']}"
                  + (f" (발표 {rec['filing_date']})" if rec["status"] == "evaluated" else ""))

            if rec["status"] == "evaluated":
                local_path = out_dir / f"dt={dt}" / f"symbol={sym}.json"
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2))
                if args.upload:
                    write_json(args.bucket, s3_out_key, rec)

    print_summary(records)
    print(f"\n로컬 저장: {out_dir}" + ("  (S3 업로드 완료)" if args.upload else "  (--upload 미지정 — S3 저장 안 함)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
