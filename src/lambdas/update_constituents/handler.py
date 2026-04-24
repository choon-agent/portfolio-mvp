"""Lambda: update_constituents.

주 1회 실행 (EventBridge). 흐름:
  1. FMP 에서 current + historical S&P 500 조회
  2. 표준 구성종목 리스트 빌드
  3. S3 의 이전 상태와 비교 (diff)
  4. 변경 있으면:
     a. 타임스탬프 스냅샷 저장
     b. 변경 이벤트를 로그에 append
     c. current 포인터 덮어쓰기
     d. 신규 편입 종목의 OHLCV 수집 
  5. 요약 정보를 구조화 JSON 로그로 출력

환경변수:
  S3_BUCKET           — 모든 상태를 저장할 버킷
  FMP_SECRET_ID       — FMP API 키의 Secrets Manager id
  AWS_REGION          — (Lambda 런타임이 자동 설정)
  CONSTITUENTS_PREFIX — 기본값 "metadata/constituents"
  OHLCV_PREFIX        — 기본값 "ohlcv"
  EVENTS_KEY          — 기본값 "metadata/constituents_changes.parquet"
  LOG_LEVEL           — 기본값 "INFO"

IAM 요구사항:
  S3_BUCKET/* 에 대한 s3:GetObject, s3:PutObject, s3:DeleteObject, s3:HeadObject
  FMP 시크릿에 대한 secretsmanager:GetSecretValue
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

# Lambda 가 /src 를 루트로 번들링하면 같은 레벨 패키지를 import 가능하도록 경로 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common.fmp_client import FMPClient, FMPError  # noqa: E402
from common.ohlcv import fetch_and_store_ohlcv  # noqa: E402
from common.s3_io import (  # noqa: E402
    append_parquet,
    get_secret,
    read_parquet,
    write_parquet_atomic,
)
from screening.constituents import (  # noqa: E402
    arrow_to_constituents,
    build_constituents,
    compute_diff,
    constituents_to_arrow,
    diff_to_events,
    events_to_arrow,
)

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
        "events_key": os.environ.get("EVENTS_KEY", "metadata/constituents_changes.parquet"),
    }


# ---------- 핸들러 ----------


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    cfg = _cfg()
    run_date = datetime.now(timezone.utc).date()

    # 1. 시크릿 & 클라이언트
    fmp_api_key = get_secret(cfg["secret_id"])
    fmp = FMPClient(api_key=fmp_api_key)

    # 2. 데이터 조회
    try:
        current_raw = fmp.get_current_sp500()
        historical_raw = fmp.get_historical_sp500()
    except FMPError as exc:
        logger.exception("FMP 조회 실패; 실행 중단")
        return {"status": "error", "stage": "fetch", "message": str(exc)}

    new_state = build_constituents(current_raw, historical_raw)
    logger.info(
        json.dumps(
            {
                "stage": "fetched",
                "current_members": sum(1 for c in new_state if c.is_current),
                "total_records": len(new_state),
            }
        )
    )

    # 3. 이전 상태 로드
    current_key = f"{cfg['constituents_prefix']}/current.parquet"
    prev_table = read_parquet(cfg["bucket"], current_key)
    prev_state = arrow_to_constituents(prev_table) if prev_table is not None else None

    # 4. Diff
    diff = compute_diff(prev_state, new_state)

    summary: dict[str, Any] = {
        "run_date": run_date.isoformat(),
        "added": diff.added_symbols,
        "removed": diff.removed_symbols,
        "metadata_changed": [c.symbol for c in diff.metadata_changed],
        "total_current_members": sum(1 for c in new_state if c.is_current),
        "is_bootstrap": prev_state is None,
    }

    # 최초 실행(bootstrap): 로깅할 변경은 없지만, 다음 실행의 기준선을 위해 상태를 저장
    if prev_state is None:
        logger.info(json.dumps({"stage": "bootstrap", **summary}))
        _persist_state(cfg, new_state, run_date)
        # Bootstrap 시에는 ~1000 종목의 OHLCV 를 여기서 수집하지 않음.
        # 일회성 백필 작업으로 분리 — Lambda 15분 타임아웃 회피.
        summary["ohlcv_fetched"] = []
        summary["status"] = "bootstrapped"
        return summary

    if not diff.has_changes:
        logger.info(json.dumps({"stage": "no_changes", **summary}))
        summary["status"] = "no_changes"
        return summary

    # 5. 변경 사항 영속화
    _persist_state(cfg, new_state, run_date)
    _append_change_events(cfg, diff, run_date)

    # 6. 신규 편입 종목의 OHLCV 수집 (옵션 A: 인라인)
    ohlcv_results: dict[str, int] = {}
    ohlcv_errors: dict[str, str] = {}
    for ticker in diff.added_symbols:
        try:
            n_rows = fetch_and_store_ohlcv(
                fmp=fmp,
                bucket=cfg["bucket"],
                symbol=ticker,
                prefix=cfg["ohlcv_prefix"],
            )
            ohlcv_results[ticker] = n_rows
        except Exception as exc:  # noqa: BLE001 — 최후 로깅; 개별 종목 실패가 전체 실행을 중단시키면 안 됨
            logger.exception("%s 의 OHLCV 수집 실패", ticker)
            ohlcv_errors[ticker] = str(exc)

    summary["ohlcv_fetched"] = ohlcv_results
    summary["ohlcv_errors"] = ohlcv_errors
    summary["status"] = "updated"
    logger.info(json.dumps({"stage": "completed", **summary}))
    return summary


# ---------- 헬퍼 ----------


def _persist_state(cfg: dict[str, str], new_state: list, run_date: Any) -> None:
    """스냅샷 기록 + current 포인터 덮어쓰기."""
    table = constituents_to_arrow(new_state)

    snapshot_key = f"{cfg['constituents_prefix']}/snapshots/{run_date.isoformat()}.parquet"
    write_parquet_atomic(cfg["bucket"], snapshot_key, table)

    current_key = f"{cfg['constituents_prefix']}/current.parquet"
    write_parquet_atomic(cfg["bucket"], current_key, table)

    logger.info(
        json.dumps(
            {
                "stage": "persisted",
                "snapshot_key": snapshot_key,
                "current_key": current_key,
                "rows": table.num_rows,
            }
        )
    )


def _append_change_events(cfg: dict[str, str], diff: Any, run_date: Any) -> None:
    events = diff_to_events(diff, run_date)
    if not events:
        return
    table = events_to_arrow(events)
    append_parquet(cfg["bucket"], cfg["events_key"], table)
    logger.info(
        json.dumps(
            {
                "stage": "events_appended",
                "events_key": cfg["events_key"],
                "n_events": len(events),
            }
        )
    )