"""Bull/Bear 응답 품질 평가 — DeepEval G-Eval 단발 실행 + 리포트 저장.

설계 근거: docs/02-bull-bear.md §11

용도:
- PoC 단계 인간 검토용 — pytest 형식 외에 *각 criteria 의 score / reasoning
  텍스트*를 한 번에 보고 싶을 때.
- 결과를 JSON 으로 저장해 사후 비교 (criteria 임계값 튜닝 근거).

pytest 와의 차이:
- pytest (test_bullbear_deepeval): 통과/실패 게이트. PR 회귀 감지.
- 본 스크립트:                     점수+이유 풀 리포트. 임계값 의사결정.

비용 (judge = Sonnet 4.6, 기본 셋 3 criteria — docs §11.5 baseline):
- 골든 8건 × 3 criteria ≈ $0.37
- 단일 (--symbols JPM) ≈ $0.045

실행:
  PYTHONPATH=src ANTHROPIC_API_KEY=sk-... .venv/bin/python scripts/run_bullbear_deepeval.py

옵션:
  --symbols AAPL,JPM         # 일부 종목만 (대문자, 골든 파일명 prefix 기준)
  --stances bull             # bull 만 (또는 bear)
  --output report.json       # 리포트 저장 경로 (기본: tests/golden/bullbear/reports/deepeval_report.json)
  --no-save                  # stdout 만, 저장 X

리포트 형식:
  {
    "judge_model": "claude-sonnet-4-6",
    "results": [
      {
        "snapshot": "JPM_bull",
        "criteria": "evidence_grounded",
        "score": 0.92,
        "threshold": 0.8,
        "passed": true,
        "reason": "..."
      }, ...
    ],
    "summary": {"total": 32, "passed": 30, "failed": 2, "pass_rate": 0.94}
  }
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.bull_bear.evaluation.adapters import (  # noqa: E402
    GOLDEN_DIR,
    AnthropicJudge,
    discover_snapshots,
    load_snapshot,
    snapshot_to_test_case,
)
from agents.bull_bear.evaluation.criteria import build_all_criteria  # noqa: E402


# 골든 디렉토리 *하위* reports/ 에 저장 — top-level 에 두면 test_bullbear_golden
# 의 glob("*.json") snapshot 탐색이 리포트를 잘못 픽업 (BullBearOpinion schema
# 검증 실패). subdirectory 는 glob 가 무시 → 자연스러운 격리.
DEFAULT_OUTPUT = GOLDEN_DIR / "reports" / "deepeval_report.json"
DEFAULT_STANCES = ("bull", "bear")


def _parse_csv(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    p.add_argument(
        "--symbols",
        type=_parse_csv,
        default=None,
        help="쉼표 구분 종목 prefix (예: AAPL,JPM). 기본: 골든 디렉토리 전체.",
    )
    p.add_argument(
        "--stances",
        type=_parse_csv,
        default=list(DEFAULT_STANCES),
        help="bull,bear / bull / bear. 기본: bull,bear",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"리포트 저장 경로. 기본: {DEFAULT_OUTPUT.relative_to(ROOT)}",
    )
    p.add_argument(
        "--no-save",
        action="store_true",
        help="stdout 만 출력, 파일 저장 X",
    )
    return p


def _filter_snapshots(
    paths: list[Path], symbols: list[str] | None, stances: list[str]
) -> list[Path]:
    """파일명 {SYMBOL}_{stance}.json 패턴 필터."""
    selected: list[Path] = []
    for path in paths:
        stem = path.stem  # 예: "JPM_bull"
        if "_" not in stem:
            continue
        sym, stance = stem.rsplit("_", 1)
        if symbols is not None and sym not in symbols:
            continue
        if stance not in stances:
            continue
        selected.append(path)
    return selected


def _evaluate_snapshot(
    snapshot_path: Path, judge: Any
) -> list[dict[str, Any]]:
    """단일 snapshot 에 4 criteria 채점 → 결과 dict 리스트."""
    from deepeval import evaluate
    from deepeval.evaluate import AsyncConfig, DisplayConfig

    snapshot = load_snapshot(snapshot_path)
    test_case = snapshot_to_test_case(snapshot, name=snapshot_path.stem)
    metrics = build_all_criteria(judge)

    # DeepEval 의 evaluate() 는 콘솔에 표 형태로 진행상황을 그려준다 — PoC 에서
    # 유용. async_config off 로 안전한 순차 실행 (judge 호출 rate limit 회피).
    result = evaluate(
        test_cases=[test_case],
        metrics=metrics,
        async_config=AsyncConfig(run_async=False),
        display_config=DisplayConfig(show_indicator=True, print_results=False),
    )

    # evaluate() 반환 구조는 deepeval 버전마다 약간 다름 — defensive 접근.
    rows: list[dict[str, Any]] = []
    test_results = getattr(result, "test_results", None) or result  # 일부 버전은 리스트 직접 반환
    for tr in (test_results if isinstance(test_results, list) else [test_results]):
        metrics_data = getattr(tr, "metrics_data", None) or getattr(tr, "metrics", [])
        for m in metrics_data:
            rows.append(
                {
                    "snapshot": snapshot_path.stem,
                    "criteria": getattr(m, "name", "unknown"),
                    "score": getattr(m, "score", None),
                    "threshold": getattr(m, "threshold", None),
                    "passed": bool(getattr(m, "success", False)),
                    "reason": getattr(m, "reason", None),
                }
            )
    return rows


def _print_row(row: dict[str, Any]) -> None:
    flag = "PASS" if row["passed"] else "FAIL"
    score = f"{row['score']:.2f}" if isinstance(row["score"], (int, float)) else "n/a"
    thr = f"{row['threshold']:.2f}" if isinstance(row["threshold"], (int, float)) else "n/a"
    print(f"  [{flag}] {row['criteria']:<32} score={score} (thr={thr})")
    if row.get("reason"):
        # reasoning 첫 200자만 콘솔에 노출 — 전체는 리포트 JSON 에.
        reason = row["reason"].replace("\n", " ")
        if len(reason) > 200:
            reason = reason[:200] + "…"
        print(f"         reason: {reason}")


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    if os.environ.get("ANTHROPIC_API_KEY") is None:
        print("ERROR: ANTHROPIC_API_KEY 환경변수 필요.", file=sys.stderr)
        return 2

    invalid_stances = [s for s in args.stances if s not in DEFAULT_STANCES]
    if invalid_stances:
        parser.error(f"미정의 stance: {invalid_stances}. 사용 가능: {list(DEFAULT_STANCES)}")

    all_snapshots = discover_snapshots()
    if not all_snapshots:
        print(
            f"ERROR: 골든 스냅샷 없음 — {GOLDEN_DIR}. "
            "scripts/run_bullbear_golden.py 먼저 실행.",
            file=sys.stderr,
        )
        return 2

    targets = _filter_snapshots(all_snapshots, args.symbols, args.stances)
    if not targets:
        print(
            f"ERROR: 필터 결과 0건 — symbols={args.symbols}, stances={args.stances}",
            file=sys.stderr,
        )
        return 2

    # 기본 criteria 셋이 3개 — criteria.build_all_criteria() 와 동기. 본 스크립트는
    # build_all_criteria 를 _evaluate_snapshot 내에서 호출하므로 그 길이가 단일
    # 출처. 콘솔 미리보기만 하드코딩 (display, 결정 영향 X).
    n_criteria = 3
    print(f"Judge: claude-sonnet-4-6")
    print(
        f"Targets: {len(targets)} snapshots × {n_criteria} criteria "
        f"= {len(targets) * n_criteria} judge calls"
    )
    # judge 호출당 ~$0.015 (Sonnet 4.6, input ~2K + output ~500 tok 기준)
    print(f"Estimated cost: ~${len(targets) * n_criteria * 0.015:.2f}\n")

    judge = AnthropicJudge()
    all_rows: list[dict[str, Any]] = []

    for path in targets:
        print(f"--- {path.stem} ---")
        try:
            rows = _evaluate_snapshot(path, judge)
        except Exception as exc:  # noqa: BLE001 — 평가 실패는 다음 종목 계속
            print(f"  ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        for row in rows:
            _print_row(row)
        all_rows.extend(rows)
        print()

    # ---- 요약 ----
    total = len(all_rows)
    passed = sum(1 for r in all_rows if r["passed"])
    failed = total - passed
    pass_rate = (passed / total) if total else 0.0

    print("=" * 60)
    print(f"Total: {total} | Passed: {passed} | Failed: {failed} | Pass rate: {pass_rate:.1%}")

    if args.no_save:
        return 0 if failed == 0 else 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "judge_model": "claude-sonnet-4-6",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "targets": [p.stem for p in targets],
        "results": all_rows,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": pass_rate,
        },
    }
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nReport saved: {args.output.relative_to(ROOT)}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
