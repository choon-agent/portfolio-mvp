"""월간 비용 리포트 — CHARTER §2.2 "월말 비용 리포트" (Anthropic + AWS).

집계 소스:
- **Anthropic (LLM)**: S3 산출물의 attempts[].cost_usd 합산 — 재시도 포함 실비용.
  bullbear (agents/bullbear/dt=*/symbol=*/stance=*.json) + scenario
  (scenarios/dt=*/symbol=*.json). 캐시 hit 은 attempts 가 그 dt 에 없으므로 자연 제외.
  한계: DeepEval judge 호출(로컬 수동)은 미포함 — 실행 시 별도 기록.
- **AWS**: Cost Explorer 월 총액 + 서비스별 상위 (ce:GetCostAndUsage 권한 필요).

사용:
  .venv/bin/python scripts/run_cost_report.py [--month 2026-08] [--upload]
  --upload 시 s3://{bucket}/reports/cost/{YYYY-MM}.md 저장.

출력: retro_data/cost_reports/{YYYY-MM}.md + 콘솔 요약.
Charter §2.2 상한 $200 대비 사용률 표기. LLM 호출 0 — 순수 집계.

자동화 참고: PoC 는 로컬 수동 (trigger batch 와 동일 패턴 — 월 1회 실행).
월간 EventBridge + Lambda 승격은 #13 자동화 결정과 함께 (infra/README).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from common.s3_io import read_json, write_text  # noqa: E402

CHARTER_CAP_USD = 200.0
_s3 = boto3.client("s3")


def _list_keys(bucket: str, prefix: str) -> list[str]:
    keys = []
    for page in _s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys += [o["Key"] for o in page.get("Contents", [])]
    return keys


def _month_dts(keys: list[str], month: str) -> set[str]:
    """키 목록에서 dt=YYYY-MM-* 파티션 중 해당 월만."""
    out = set()
    for k in keys:
        if f"dt={month}-" in k:
            out.add(k)
    return out


def llm_costs(bucket: str, month: str) -> dict[str, float]:
    """S3 산출물 attempts 합산 — {용도: 비용}."""
    costs = {"bullbear": 0.0, "scenario": 0.0}
    for key in _month_dts(_list_keys(bucket, "agents/bullbear/dt="), month):
        if not key.endswith(".json") or "stance=" not in key:
            continue
        doc = read_json(bucket, key) or {}
        costs["bullbear"] += sum(a.get("cost_usd", 0.0) for a in doc.get("attempts", []))
    for key in _month_dts(_list_keys(bucket, "scenarios/dt="), month):
        doc = read_json(bucket, key) or {}
        costs["scenario"] += sum(a.get("cost_usd", 0.0) for a in doc.get("attempts", []))
    return costs


def aws_costs(month: str) -> tuple[float, list[tuple[str, float]]] | None:
    """Cost Explorer 월 총액 + 서비스 상위 5. 권한 없으면 None (리포트에 명시)."""
    y, m = map(int, month.split("-"))
    start = f"{month}-01"
    end = f"{y + (m == 12)}-{(m % 12) + 1:02d}-01"
    try:
        ce = boto3.client("ce", region_name="us-east-1")
        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": start, "End": end},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
        groups = resp["ResultsByTime"][0]["Groups"]
        by_service = sorted(
            ((g["Keys"][0], float(g["Metrics"]["UnblendedCost"]["Amount"])) for g in groups),
            key=lambda kv: -kv[1],
        )
        total = sum(v for _, v in by_service)
        return total, by_service[:5]
    except Exception as exc:  # ce 미활성/권한 없음 — 리포트에 사유 기록
        print(f"[WARN] Cost Explorer 조회 실패: {exc}", file=sys.stderr)
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default=date.today().strftime("%Y-%m"))
    parser.add_argument("--bucket", default=os.environ.get("S3_BUCKET", "portfolio-mvp-data-s3"))
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()

    llm = llm_costs(args.bucket, args.month)
    llm_total = sum(llm.values())
    aws = aws_costs(args.month)

    lines = [
        f"# 비용 리포트 — {args.month}",
        "",
        f"> 생성: {datetime.now(timezone.utc).isoformat()} / CHARTER §2.2 상한 **${CHARTER_CAP_USD:.0f}/월**",
        "",
        "## Anthropic (LLM) — S3 산출물 attempts 실비용 합산",
        "",
        "| 용도 | 비용 |",
        "|---|---|",
        f"| Bull/Bear (2단계) | ${llm['bullbear']:.2f} |",
        f"| Scenario (3단계) | ${llm['scenario']:.2f} |",
        f"| **합계** | **${llm_total:.2f}** ({llm_total / CHARTER_CAP_USD:.1%} of cap) |",
        "",
        "- 1·4단계 LLM 0 (CHARTER §3.3). DeepEval judge(로컬 수동)는 미포함.",
        "",
        "## AWS (Cost Explorer, UnblendedCost)",
        "",
    ]
    if aws is None:
        lines.append("- 조회 실패 (ce:GetCostAndUsage 권한 또는 Cost Explorer 미활성) — 콘솔에서 확인 필요")
    else:
        total, top = aws
        lines.append(f"**월 총액: ${total:.2f}** (계정 공용 — 타 프로젝트(choon-* 등) 비용 포함 가능)")
        lines.append("")
        lines += ["| 서비스 | 비용 |", "|---|---|"]
        lines += [f"| {name} | ${amt:.2f} |" for name, amt in top if amt >= 0.005]
    lines += [
        "",
        "## 판정",
        "",
        f"- LLM ${llm_total:.2f} / $200 — {'✅ 이내' if llm_total < CHARTER_CAP_USD else '🔴 초과 → §2.2 조치 순서 (에이전트 축소 → Haiku → 빈도 축소) + scripts/emergency_stop.sh'}",
    ]
    report = "\n".join(lines) + "\n"

    out = ROOT / "retro_data" / "cost_reports" / f"{args.month}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(report)
    print(f"로컬 저장: {out}")
    if args.upload:
        key = f"reports/cost/{args.month}.md"
        write_text(args.bucket, key, report, content_type="text/markdown")
        print(f"S3 저장: s3://{args.bucket}/{key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
