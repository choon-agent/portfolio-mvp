"""Lambda: update_ohlcv.

매 거래일 종료 후 실행 (EventBridge). 흐름:
  1. current.parquet 에서 현재 구성종목 심볼 목록 확보 (~503개)
  2. 각 심볼에 대해 기존 parquet 의 마지막 날짜 이후 행만 FMP 로부터 받아 append
  3. 요약 JSON 반환

환경변수:
  S3_BUCKET           — 필수
  FMP_SECRET_ID       — 필수
  CONSTITUENTS_PREFIX — 기본 "metadata/constituents"
  OHLCV_PREFIX        — 기본 "ohlcv"
  EXTRA_SYMBOLS       — 기본 "SPY" (쉼표 구분) — 구성종목 외 추가 수집.
                        SPY 는 5단계 벤치마크 (docs/05-rebalancing.md §6, 검토 포인트 ④)
  LOG_LEVEL           — 기본 "INFO"

IAM 요구사항:
  S3_BUCKET/* 에 대한 s3:GetObject, s3:PutObject, s3:DeleteObject, s3:HeadObject, s3:ListBucket
  FMP 시크릿에 대한 secretsmanager:GetSecretValue

성능:
  503 심볼 × (FMP ~0.5s + 기존 parquet 읽기 ~0.1s + S3 write ~0.2s) ≈ 6~8분.
  Lambda 15분 타임아웃 안에 안정적. Memory: 512MB 충분.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests

# Lambda 가 /src 를 루트로 번들링하면 같은 레벨 패키지를 import 가능하도록 경로 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common.fmp_client import FMPClient, FMPError  # noqa: E402
from common.ohlcv import update_ohlcv_incremental  # noqa: E402
from common.s3_io import get_secret, read_parquet  # noqa: E402
from screening.constituents import arrow_to_constituents  # noqa: E402

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


# ---------- 설정 ----------


def _cfg() -> dict[str, str]:
    bucket = os.environ.get("S3_BUCKET")
    secret_id = os.environ.get("FMP_SECRET_ID")
    if not bucket or not secret_id:
        raise RuntimeError("환경변수 S3_BUCKET 과 FMP_SECRET_ID 는 필수입니다")
    return {
        "bucket": bucket,
        "secret_id": secret_id,
        "constituents_prefix": os.environ.get("CONSTITUENTS_PREFIX", "metadata/constituents"),
        "ohlcv_prefix": os.environ.get("OHLCV_PREFIX", "ohlcv"),
    }


def _load_symbols(cfg: dict[str, str]) -> list[str]:
    key = f"{cfg['constituents_prefix']}/current.parquet"
    table = read_parquet(cfg["bucket"], key)
    if table is None:
        raise RuntimeError(
            f"s3://{cfg['bucket']}/{key} 없음. update_constituents Lambda 를 먼저 실행해서 "
            "bootstrap 스냅샷을 생성하세요."
        )
    constituents = arrow_to_constituents(table)
    extra = {
        s.strip() for s in os.environ.get("EXTRA_SYMBOLS", "SPY").split(",") if s.strip()
    }
    return sorted({c.symbol for c in constituents if c.is_current} | extra)


# ---------- 핸들러 ----------


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    cfg = _cfg()
    run_date = datetime.now(timezone.utc).date()

    fmp = FMPClient(api_key=get_secret(cfg["secret_id"]))
    symbols = _load_symbols(cfg)
    logger.info(json.dumps({"stage": "start", "n_symbols": len(symbols), "run_date": run_date.isoformat()}))

    updated: list[str] = []
    unchanged: list[str] = []
    failed: dict[str, str] = {}
    total_added = 0
    start = time.time()

    for i, symbol in enumerate(symbols, 1):
        try:
            result = update_ohlcv_incremental(
                fmp=fmp,
                bucket=cfg["bucket"],
                symbol=symbol,
                prefix=cfg["ohlcv_prefix"],
            )
            if result["added"] > 0:
                updated.append(symbol)
                total_added += result["added"]
            else:
                unchanged.append(symbol)
        except (FMPError, requests.RequestException) as exc:
            failed[symbol] = f"{type(exc).__name__}: {exc}"
            logger.warning("[%d/%d] %s 실패: %s", i, len(symbols), symbol, exc)
        except Exception as exc:  # noqa: BLE001 — 개별 실패가 전체 중단을 막아야 함
            failed[symbol] = f"{type(exc).__name__}: {exc}"
            logger.exception("[%d/%d] %s 예상치 못한 오류", i, len(symbols), symbol)

    elapsed = time.time() - start

    summary: dict[str, Any] = {
        "status": "completed" if not failed else "partial",
        "run_date": run_date.isoformat(),
        "n_symbols": len(symbols),
        "n_updated": len(updated),
        "n_unchanged": len(unchanged),
        "n_failed": len(failed),
        "total_rows_added": total_added,
        "elapsed_seconds": round(elapsed, 1),
    }
    logger.info(json.dumps({"stage": "completed", **summary}))

    # 구조화 로그에는 실패 상세까지 포함 (반환값 크기 제한 고려해 별도 키로)
    summary["failed"] = failed
    return summary
