"""S&P 500 구성종목의 OHLCV 일회성 백필.

사용 이유:
  - update_constituents Lambda 의 bootstrap 실행은 OHLCV 수집을 의도적으로 건너뜀
    (503 종목 × FMP 호출 시 Lambda 15분 타임아웃 위험)
  - 이 스크립트는 로컬에서 실행하여 한 번만 전체 OHLCV 를 채움
  - 이후 주간 Lambda 는 신규 편입분만 수집하므로 Lambda 범위 내 동작

전제:
  - S3 의 metadata/constituents/current.parquet 이미 존재 (Lambda 최초 실행 완료)
  - 로컬 AWS 자격증명 설정 완료 (aws configure 또는 SSO)
  - FMP API 키가 Secrets Manager 에 저장되어 있음

실행 예:
  # 기본 (S3_BUCKET / FMP_SECRET_ID 환경변수 사용)
  python scripts/backfill_ohlcv.py

  # 특정 심볼만 (테스트용)
  python scripts/backfill_ohlcv.py --symbols AAPL,MSFT,GOOGL

  # 처음 N 개만 (샘플링)
  python scripts/backfill_ohlcv.py --limit 10

  # 이미 받은 심볼은 건너뛰기 (재시도 용)
  python scripts/backfill_ohlcv.py --resume
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from common.fmp_client import FMPClient, FMPError  # noqa: E402
from common.ohlcv import fetch_and_store_ohlcv  # noqa: E402
from common.s3_io import get_secret, object_exists, read_parquet  # noqa: E402
from screening.constituents import arrow_to_constituents  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("backfill_ohlcv")


def _load_symbols(bucket: str, prefix: str) -> list[str]:
    """S3 의 current.parquet 에서 현재 구성원(date_removed=None) 심볼 목록 반환."""
    key = f"{prefix}/current.parquet"
    table = read_parquet(bucket, key)
    if table is None:
        raise SystemExit(
            f"S3 에 {key} 가 없음. 먼저 update_constituents Lambda 를 실행해서 "
            "bootstrap 시키세요."
        )
    constituents = arrow_to_constituents(table)
    return sorted({c.symbol for c in constituents if c.is_current})


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bucket", default=os.environ.get("S3_BUCKET"), help="S3 버킷 (기본: $S3_BUCKET)")
    p.add_argument("--secret-id", default=os.environ.get("FMP_SECRET_ID"), help="FMP 시크릿 ID (기본: $FMP_SECRET_ID)")
    p.add_argument("--constituents-prefix", default=os.environ.get("CONSTITUENTS_PREFIX", "metadata/constituents"))
    p.add_argument("--ohlcv-prefix", default=os.environ.get("OHLCV_PREFIX", "ohlcv"))
    p.add_argument("--symbols", help="쉼표 구분 심볼 목록. 지정 시 current.parquet 대신 이 목록 사용")
    p.add_argument("--limit", type=int, help="처음 N 개만 처리 (디버깅용)")
    p.add_argument("--resume", action="store_true", help="이미 S3 에 있는 심볼은 건너뛰기")
    p.add_argument("--sleep", type=float, default=0.2, help="심볼 사이 대기 시간 (초). FMP rate limit 여유분")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    if not args.bucket or not args.secret_id:
        logger.error("--bucket 과 --secret-id 또는 환경변수 S3_BUCKET / FMP_SECRET_ID 필요")
        return 2

    symbols = (
        [s.strip() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else _load_symbols(args.bucket, args.constituents_prefix)
    )
    if args.limit:
        symbols = symbols[: args.limit]

    logger.info("대상 심볼 %d 개 (bucket=%s, ohlcv_prefix=%s)", len(symbols), args.bucket, args.ohlcv_prefix)

    fmp = FMPClient(api_key=get_secret(args.secret_id))

    succeeded: list[str] = []
    skipped: list[str] = []
    failed: dict[str, str] = {}
    start = time.time()

    for i, symbol in enumerate(symbols, 1):
        key = f"{args.ohlcv_prefix}/ticker={symbol}/data.parquet"

        if args.resume and object_exists(args.bucket, key):
            logger.info("[%d/%d] %s — 이미 존재, 건너뜀", i, len(symbols), symbol)
            skipped.append(symbol)
            continue

        try:
            n_rows = fetch_and_store_ohlcv(
                fmp=fmp,
                bucket=args.bucket,
                symbol=symbol,
                prefix=args.ohlcv_prefix,
            )
            if n_rows == 0:
                failed[symbol] = "데이터 없음"
                logger.warning("[%d/%d] %s — 행 0개", i, len(symbols), symbol)
            else:
                succeeded.append(symbol)
                elapsed = time.time() - start
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(symbols) - i) / rate if rate > 0 else 0
                logger.info(
                    "[%d/%d] %s — %d rows (누적 %.0fs, 속도 %.1f/s, ETA %.0fs)",
                    i, len(symbols), symbol, n_rows, elapsed, rate, eta,
                )
        except FMPError as exc:
            failed[symbol] = f"FMPError: {exc}"
            logger.exception("[%d/%d] %s — FMP 호출 실패", i, len(symbols), symbol)
        except Exception as exc:  # noqa: BLE001 — 개별 실패가 전체 중단을 막아야 함
            failed[symbol] = f"{type(exc).__name__}: {exc}"
            logger.exception("[%d/%d] %s — 예상치 못한 오류", i, len(symbols), symbol)

        if args.sleep > 0 and i < len(symbols):
            time.sleep(args.sleep)

    total_elapsed = time.time() - start
    logger.info("=" * 60)
    logger.info("완료 — 총 %.0fs", total_elapsed)
    logger.info("성공: %d | 건너뜀(resume): %d | 실패: %d", len(succeeded), len(skipped), len(failed))
    if failed:
        logger.warning("실패 목록 (재시도 시 --symbols 에 넘기기):")
        for sym, reason in failed.items():
            logger.warning("  %s: %s", sym, reason)
        print(",".join(failed.keys()))

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
