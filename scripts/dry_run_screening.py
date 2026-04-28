"""M1 dry-run — 30종목 (또는 사용자 지정) 실제 FMP 데이터로 pipeline.run_screening 실행 + 검증.

목적:
- FMP key-metrics-ttm + historical-price + profile 통합 (factors.py, universe.py)
- pipeline.run_screening 의 엔드투엔드 동작 (운영 target_min/max=15/20)
- normalize.py 의 sub_sector → sector → universe 폴백 검증
- ScreeningResult 직렬화 + Bull/Bear 핸드오프 contract

기본 종목: 6 sub-industry 에 5종목씩 = 30종목. 5개씩 그룹은 sub_sector 그룹화 그대로,
혼합 sub_sector 가 있으면 sector 폴백 검증.

sector/sub_sector 는 FMP profile 엔드포인트에서 실측. 운영 Lambda 는 S3 캐시
(constituents)에서 가져오므로 본 스크립트의 profile 호출은 dry-run 한정.

사용:
    export FMP_API_KEY=your_key
    python scripts/dry_run_screening.py                              # 기본 30
    python scripts/dry_run_screening.py JPM BAC WFC                  # 커스텀 (작은 입력은 --target-min/max 명시)
    python scripts/dry_run_screening.py --output build/result.json
    python scripts/dry_run_screening.py --as-of-date 2026-04-25
    python scripts/dry_run_screening.py JPM BAC WFC C USB --target-min 3 --target-max 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from common.fmp_client import FMPClient, FMPError  # noqa: E402
from common.models import Constituent  # noqa: E402
from common.ohlcv import ohlcv_to_arrow  # noqa: E402
from screening.pipeline import run_screening  # noqa: E402

# 기본 30종목 — 6 그룹 × 5종목.
# 그룹화 의도(GICS 기준 추정): 같은 sub_sector 5개씩이면 normalize 가 sub_sector 그룹 사용.
# FMP industry 명명이 다소 다를 수 있어 실제 분포는 profile 응답으로 확인.
DEFAULT_SYMBOLS: list[str] = [
    # Application Software
    "MSFT", "ADBE", "ORCL", "CRM", "NOW",
    # Semiconductors
    "NVDA", "AMD", "AVGO", "INTC", "TXN",
    # Diversified Banks
    "JPM", "BAC", "WFC", "C", "USB",
    # Pharmaceuticals
    "JNJ", "PFE", "MRK", "BMY", "ABBV",
    # Energy (Integrated / Exploration 혼합 — sector 폴백 가능성 있음)
    "XOM", "CVX", "COP", "EOG", "MPC",
    # Health Care Plans (헬스케어 — Pharma 와 다른 sub_sector)
    "UNH", "ELV", "CI", "HUM", "CVS",
]


def _build_constituent(symbol: str, *, name: str, sector: str, sub_sector: str) -> Constituent:
    return Constituent(
        symbol=symbol,
        company_name=name,
        sector=sector,
        sub_sector=sub_sector,
        date_added=date(2000, 1, 1),  # universe.is_seasoned 통과
    )


def _fetch_one(
    fmp: FMPClient, symbol: str
) -> tuple[Any, dict[str, Any] | None, dict[str, Any] | None]:
    """OHLCV pa.Table + key-metrics-ttm dict + profile dict (없으면 None)."""
    try:
        rows = fmp.get_historical_price(symbol)
        ohlcv = ohlcv_to_arrow(rows) if rows else None
    except FMPError as exc:
        print(f"  [WARN] {symbol} OHLCV 실패: {exc}")
        ohlcv = None

    try:
        km_resp = fmp.get_key_metrics_ttm(symbol)
        km = km_resp[0] if km_resp else None
    except FMPError as exc:
        print(f"  [WARN] {symbol} key-metrics-ttm 실패: {exc}")
        km = None

    try:
        prof_resp = fmp.get_profile(symbol)
        prof = prof_resp[0] if prof_resp else None
    except FMPError as exc:
        print(f"  [WARN] {symbol} profile 실패: {exc}")
        prof = None

    return ohlcv, km, prof


def _profile_to_metadata(symbol: str, profile: dict[str, Any] | None) -> dict[str, str]:
    """profile 응답에서 sector, industry(=sub_sector), companyName 추출.

    profile 이 없으면 모두 'Unknown' — universe filter 통과는 가능하지만
    normalize 의 그룹화에서 sector_unknown 그룹으로 묶임.
    """
    if not profile:
        return {"name": symbol, "sector": "Unknown", "sub_sector": "Unknown"}
    return {
        "name": profile.get("companyName") or symbol,
        "sector": profile.get("sector") or "Unknown",
        "sub_sector": profile.get("industry") or "Unknown",
    }


def _verify_bull_bear_contract(result_dict: dict[str, Any]) -> list[str]:
    """docs/01-screening.md 부록 A 의 Bull/Bear 입력 계약 검증."""
    issues: list[str] = []
    for s in result_dict["selected"]:
        sym = s["symbol"]
        for field in ("symbol", "company_name", "sector", "sub_sector"):
            if not s.get(field):
                issues.append(f"{sym}: {field} 누락")
        if not isinstance(s.get("composite_score"), (int, float)):
            issues.append(f"{sym}: composite_score 비정상")
        peers = s.get("peer_context", [])
        if not peers:
            issues.append(f"{sym}: peer_context 비어있음 (sub_sector 표본 부족 의심)")
        f = s.get("factors", {}) or {}
        if f.get("momentum_z") is None and f.get("momentum_12_1m") is None:
            issues.append(f"{sym}: 모멘텀 데이터 전무")
        if f.get("value_z") is None:
            issues.append(f"{sym}: value_z 결측 (raw 컴포넌트 부족 가능)")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="M1 dry-run — pipeline 실행")
    parser.add_argument("symbols", nargs="*", help="종목 (미지정 시 기본 30개)")
    parser.add_argument("--target-min", type=int, default=15, help="selected 하한 (기본 15 — 운영 정책)")
    parser.add_argument("--target-max", type=int, default=20, help="selected 상한 (기본 20)")
    parser.add_argument("--as-of-date", help="기준일 YYYY-MM-DD (기본 today UTC)")
    parser.add_argument("--output", help="result.json 저장 경로 (미지정 시 stdout)")
    args = parser.parse_args()

    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        print("ERROR: FMP_API_KEY 환경변수가 설정되지 않음", file=sys.stderr)
        return 1

    symbols = args.symbols or DEFAULT_SYMBOLS

    if len(symbols) < args.target_min:
        print(
            f"[WARN] 입력 {len(symbols)}종목이 target_min({args.target_min}) 미만 — "
            f"--target-min {min(3, len(symbols))} --target-max {len(symbols)} 처럼 명시 필요",
            file=sys.stderr,
        )

    as_of = (
        datetime.strptime(args.as_of_date, "%Y-%m-%d").date()
        if args.as_of_date
        else datetime.now(timezone.utc).date()
    )

    print("=" * 70)
    print(f"M1 dry-run — {len(symbols)} 종목, as_of={as_of}, target={args.target_min}~{args.target_max}")
    print("=" * 70)

    fmp = FMPClient(api_key=api_key)

    # 1. FMP 데이터 조회 + 입력 조립
    constituents: list[Constituent] = []
    market_caps: dict[str, float | None] = {}
    price_histories: dict[str, Any] = {}
    key_metrics_ttm: dict[str, dict[str, Any] | None] = {}

    sub_sector_counts: Counter[str] = Counter()

    for i, symbol in enumerate(symbols, start=1):
        ohlcv, km, prof = _fetch_one(fmp, symbol)
        meta = _profile_to_metadata(symbol, prof)

        constituents.append(_build_constituent(symbol, **meta))
        price_histories[symbol] = ohlcv
        key_metrics_ttm[symbol] = km

        cap = None
        if km and km.get("marketCap") is not None:
            try:
                cap = float(km["marketCap"])
            except (TypeError, ValueError):
                cap = None
        market_caps[symbol] = cap

        sub_sector_counts[meta["sub_sector"]] += 1

        n_rows = ohlcv.num_rows if ohlcv is not None else 0
        cap_b = f"${cap / 1e9:>7.1f}B" if cap else "        —"
        print(
            f"  [{i:>2}/{len(symbols)}] {symbol:<6} "
            f"OHLCV {n_rows:>5}일  cap={cap_b}  "
            f"{meta['sector']:<22} / {meta['sub_sector']}"
        )

    # 입력 분포 요약
    print()
    print("입력 sub_sector 분포 (FMP profile 기준):")
    for sub, n in sub_sector_counts.most_common():
        print(f"  {n:>2} × {sub}")

    # 2. 파이프라인 실행
    print()
    print("=" * 70)
    print("pipeline.run_screening 실행")
    print("=" * 70)
    try:
        result = run_screening(
            constituents=constituents,
            market_caps=market_caps,
            price_histories=price_histories,
            key_metrics_ttm=key_metrics_ttm,
            as_of_date=as_of,
            target_min=args.target_min,
            target_max=args.target_max,
        )
    except ValueError as exc:
        print(f"  [FAIL] pipeline ValueError: {exc}")
        return 1

    print(f"  universe_size  : {result.universe_size} (필터 통과)")
    print(f"  selected_count : {len(result.selected)}")
    print(f"  factor_weights : {result.factor_weights}")
    print(f"  run_id         : {result.run_id}")

    # 선정 종목
    print()
    print("  선정 종목 (rank, symbol, composite, mom_z, val_z, sub_sector, flags):")
    for s in result.selected:
        flags = ",".join(s.data_quality_flags) if s.data_quality_flags else "-"
        mom_z = f"{s.factors.momentum_z:+.2f}" if s.factors.momentum_z is not None else "  —"
        val_z = f"{s.factors.value_z:+.2f}" if s.factors.value_z is not None else "  —"
        print(
            f"    {s.rank:>2}. {s.symbol:<6} {s.composite_score:+.3f}  "
            f"mom={mom_z}  val={val_z}  {s.sub_sector or '?':<28}  [{flags}]"
        )

    # 선정 sub_sector 분포 + peer_context 길이 통계
    selected_sub_counts: Counter[str] = Counter(s.sub_sector or "Unknown" for s in result.selected)
    peer_lens = [len(s.peer_context) for s in result.selected]

    print()
    print("선정 sub_sector 분포:")
    for sub, n in selected_sub_counts.most_common():
        print(f"  {n:>2} × {sub}")
    print(f"\nselected 의 peer_context 길이: min={min(peer_lens)}, max={max(peer_lens)}, "
          f"avg={sum(peer_lens)/len(peer_lens):.1f}")

    # 3. Bull/Bear contract 검증
    print()
    print("=" * 70)
    print("Bull/Bear 핸드오프 contract 검증")
    print("=" * 70)
    result_dict = json.loads(result.model_dump_json())
    issues = _verify_bull_bear_contract(result_dict)
    if issues:
        print(f"  ⚠ {len(issues)} 건 잠재 이슈:")
        for issue in issues:
            print(f"    - {issue}")
    else:
        print("  ✓ selected 모두 — symbol/sector 필드 + composite + peer_context + factors 완비")

    # 4. 결과 저장 또는 출력
    output_json = result.model_dump_json(indent=2)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(output_json, encoding="utf-8")
        print()
        print(f"result.json → {out} ({len(output_json):,} bytes)")
    else:
        print()
        print("=" * 70)
        print("result.json (요약 — 전체 보려면 --output 사용)")
        print("=" * 70)
        # 30종목이면 매우 김 → 첫 1종목만 stdout
        if len(result.selected) > 3:
            sample = result_dict["selected"][:1]
            preview = {**result_dict, "selected": sample}
            print(json.dumps(preview, indent=2, ensure_ascii=False))
            print(f"... (selected[1:{len(result.selected)}] 생략 — 총 {len(result.selected)}종목)")
        else:
            print(output_json)

    return 0 if not issues else 2  # 0=통과, 2=경고있지만 성공


if __name__ == "__main__":
    sys.exit(main())
