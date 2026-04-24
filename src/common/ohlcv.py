"""OHLCV 수집 및 S3 저장.

LLM 사용: 없음.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from common.fmp_client import FMPClient
from common.s3_io import read_parquet, write_parquet_atomic

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

    덮어쓰기 방식. 초기 백필·전체 재적재 용도.
    일일 증분 업데이트는 update_ohlcv_incremental 사용.

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


def update_ohlcv_incremental(
    fmp: FMPClient,
    bucket: str,
    symbol: str,
    prefix: str = "ohlcv",
) -> dict[str, int]:
    """기존 parquet 을 읽어 마지막 날짜 이후 행만 append.

    FMP 의 5년 이력 창을 넘어서도 S3 에 과거 데이터를 영구 보존하기 위한 증분 업데이트.
    기존 객체가 없으면 최초 저장(= fetch_and_store_ohlcv 동일 동작).

    반환:
      {"added": N, "total": M} — N 은 이번 실행에서 추가된 행, M 은 병합 후 총 행.

    주의:
      FMP 가 과거 adjClose 를 소급 수정(분할·배당 재계산)해도 기존 행은 갱신되지 않음.
      완전한 시계열 정합이 필요하면 주기적 재백필(fetch_and_store_ohlcv)로 보완.
    """
    key = f"{prefix}/ticker={symbol}/data.parquet"
    existing = read_parquet(bucket, key)

    rows = fmp.get_historical_price(symbol)
    if not rows:
        logger.warning("%s OHLCV 응답 비어있음", symbol)
        return {"added": 0, "total": existing.num_rows if existing is not None else 0}

    new_table = ohlcv_to_arrow(rows)

    if existing is None or existing.num_rows == 0:
        write_parquet_atomic(bucket, key, new_table)
        logger.info("%s 최초 저장 %d rows → s3://%s/%s", symbol, new_table.num_rows, bucket, key)
        return {"added": new_table.num_rows, "total": new_table.num_rows}

    last_date = pc.max(existing.column("date")).as_py()
    last_scalar = pa.scalar(last_date, type=pa.date32())
    delta = new_table.filter(pc.greater(new_table.column("date"), last_scalar))

    if delta.num_rows == 0:
        logger.info("%s 이미 최신 (last=%s)", symbol, last_date)
        return {"added": 0, "total": existing.num_rows}

    combined = pa.concat_tables([existing, delta])
    write_parquet_atomic(bucket, key, combined)
    logger.info(
        "%s append %d rows (total=%d, range=%s..%s)",
        symbol,
        delta.num_rows,
        combined.num_rows,
        last_date,
        pc.max(combined.column("date")).as_py(),
    )
    return {"added": delta.num_rows, "total": combined.num_rows}