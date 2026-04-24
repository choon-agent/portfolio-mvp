"""OHLCV 수집 및 S3 저장.

LLM 사용: 없음.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pyarrow as pa

from common.fmp_client import FMPClient
from common.s3_io import write_parquet_atomic

logger = logging.getLogger(__name__)

OHLCV_SCHEMA = pa.schema(
    [
        ("date", pa.date32()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("adj_close", pa.float64()),
        ("volume", pa.int64()),
    ]
)


def _parse_ohlcv_row(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return {
            "date": datetime.strptime(row["date"], "%Y-%m-%d").date(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "adj_close": float(row.get("adjClose", row["close"])),
            "volume": int(row.get("volume") or 0),
        }
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("형식 오류로 OHLCV 행 건너뜀: %s (%s)", row, exc)
        return None


def ohlcv_to_arrow(raw_rows: list[dict[str, Any]]) -> pa.Table:
    """FMP historical-price-full 응답 행을 Arrow 테이블로 변환.

    FMP 는 최신순으로 반환 — 분석 편의를 위해 오름차순 정렬.
    """
    parsed = [r for r in (_parse_ohlcv_row(row) for row in raw_rows) if r is not None]
    parsed.sort(key=lambda r: r["date"])

    data: dict[str, list[Any]] = {name: [] for name in OHLCV_SCHEMA.names}
    for r in parsed:
        for name in OHLCV_SCHEMA.names:
            data[name].append(r[name])
    return pa.table(data, schema=OHLCV_SCHEMA)


def fetch_and_store_ohlcv(
    fmp: FMPClient,
    bucket: str,
    symbol: str,
    prefix: str = "ohlcv",
) -> int:
    """한 종목의 전체 이력을 가져와서 다음 경로에 기록:
    s3://{bucket}/{prefix}/ticker={symbol}/data.parquet.

    반환: 기록된 행 수 (데이터 없으면 0).
    """
    rows = fmp.get_historical_price(symbol)
    if not rows:
        logger.warning("%s 에 대해 OHLCV 데이터가 반환되지 않음", symbol)
        return 0

    table = ohlcv_to_arrow(rows)
    key = f"{prefix}/ticker={symbol}/data.parquet"
    write_parquet_atomic(bucket, key, table)
    logger.info("%s 행 %d개 기록 완료 → s3://%s/%s", symbol, table.num_rows, bucket, key)
    return table.num_rows