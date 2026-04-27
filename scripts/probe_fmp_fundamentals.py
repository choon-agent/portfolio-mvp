"""FMP 펀더멘털 엔드포인트 가용성 점검.

스크리닝의 밸류 팩터(pe_ttm, ev_ebitda, fcf_yield)와 universe 필터의 시총 컷에
필요한 엔드포인트가 현재 FMP 요금제에서 호출 가능한지 확인.

확인 항목:
- 응답이 정상으로 도착하는지 (HTTP 2xx + 비어있지 않음)
- 우리 코드(factors.py, universe.py)가 참조하는 필드가 존재하는지

사용:
    export FMP_API_KEY=your_key
    python scripts/probe_fmp_fundamentals.py             # AAPL 1종목 (기본)
    python scripts/probe_fmp_fundamentals.py MSFT NVDA   # 다중 종목

엔드포인트 (실측 검증 — 2026-04 기준):
- quote               시총 (universe.py 의 market_caps 입력원, 선택)
- key-metrics-ttm     P/E (= 1/earningsYieldTTM), EV/EBITDA, FCF Yield + marketCap (factors.py)

ratios-ttm 은 별도 호출 불필요 — key-metrics-ttm 단일 엔드포인트가 모든 밸류 컴포넌트 제공.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# src/ 를 import path 에 추가
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from common.fmp_client import FMPClient, FMPError  # noqa: E402

PROBES: list[dict[str, Any]] = [
    {
        "endpoint": "quote",
        "needed_fields": ["marketCap", "price"],
        "used_by": "universe.py (시총 컷, 선택 — key-metrics-ttm 도 marketCap 제공)",
    },
    {
        "endpoint": "key-metrics-ttm",
        "needed_fields": [
            "marketCap",
            "earningsYieldTTM",         # 1/peRatioTTM
            "evToEBITDATTM",
            "freeCashFlowYieldTTM",
        ],
        "used_by": "factors.py (P/E, EV/EBITDA, FCF Yield TTM) + marketCap",
    },
]


def _flatten(payload: Any) -> dict[str, Any]:
    """FMP 응답이 list 면 첫 항목, dict 면 그대로."""
    if isinstance(payload, list):
        return payload[0] if payload else {}
    if isinstance(payload, dict):
        return payload
    return {}


def _check_one(client: FMPClient, symbol: str, probe: dict[str, Any]) -> int:
    """1종목 × 1엔드포인트 점검. 실패한 항목 수 반환."""
    endpoint = probe["endpoint"]
    print(f"\n[{symbol}] {endpoint}  ({probe['used_by']})")
    try:
        resp = client._get(endpoint, params={"symbol": symbol})
    except FMPError as exc:
        print(f"  ✗ 호출 실패: {exc}")
        return 1

    row = _flatten(resp)
    if not row:
        print(f"  ✗ 응답 비어 있음 (요금제 미허용 또는 종목 데이터 없음)")
        return 1

    failures = 0
    for field in probe["needed_fields"]:
        value = row.get(field)
        if value is None:
            print(f"  ✗ 누락: {field}")
            failures += 1
        else:
            # 값이 너무 길면 자름 (예: dict 형태 응답)
            display = str(value)
            if len(display) > 60:
                display = display[:57] + "..."
            print(f"  ✓ {field} = {display}")
    return failures


def main(symbols: list[str]) -> int:
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        print("ERROR: FMP_API_KEY 환경변수가 설정되지 않음", file=sys.stderr)
        return 1

    client = FMPClient(api_key=api_key)

    total_failures = 0
    print(f"점검 종목: {symbols}")
    for symbol in symbols:
        for probe in PROBES:
            total_failures += _check_one(client, symbol, probe)

    print()
    print("=" * 60)
    if total_failures:
        print(f"⚠ {total_failures} 개 항목 실패 — FMP 요금제 또는 엔드포인트 확인 필요")
        print("  FMP 대시보드에서 요금제와 사용 가능 엔드포인트를 점검하세요.")
        return 1
    print("✓ 모든 엔드포인트 호출 성공 + 필요 필드 모두 존재")
    print("  factors.py, universe.py 가 그대로 동작할 수 있습니다.")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:] or ["AAPL"]
    sys.exit(main(args))
