"""Lambda: run_screening.

매주 월요일 06:00 ET 실행 (EventBridge → Step Functions). 흐름:
  1. S3 에서 현재 구성종목 + 종목별 OHLCV 로드 (기존 update_constituents/update_ohlcv 가 채움)
  2. 종목별 key-metrics-ttm 조회 (S3 캐시 우선, miss/stale 시 FMP 호출)
  3. marketCap 을 key-metrics-ttm 응답에서 추출 (별도 quote 호출 불필요)
  4. pipeline.run_screening() 호출
  5. ScreeningResult 를 S3 저장 (Bull/Bear 다음 단계 입력)
  6. 요약 JSON 반환

이벤트 입력 (선택):
  {
    "as_of_date": "2026-05-04",     // 미지정 시 today (UTC)
    "run_id": "manual-001"          // 미지정 시 as_of 기반 ISO 타임스탬프
  }

환경변수:
  S3_BUCKET             — 필수
  FMP_SECRET_ID         — 필수
  CONSTITUENTS_PREFIX   — 기본 "metadata/constituents"
  OHLCV_PREFIX          — 기본 "ohlcv"
  FUNDAMENTALS_PREFIX   — 기본 "metadata/fundamentals/key-metrics-ttm"
  SCREENING_PREFIX      — 기본 "screening"
  CACHE_MAX_AGE_DAYS    — 기본 "90"
  LOG_LEVEL             — 기본 "INFO"

IAM 요구사항:
  S3_BUCKET/* 에 대한 s3:GetObject, s3:PutObject, s3:HeadObject
  FMP 시크릿에 대한 secretsmanager:GetSecretValue

성능:
  - 캐시 hit (대부분 주차): S3 read ~500 + 스크리닝 합산 ≈ 1~2분
  - 캐시 miss (분기 발표 첫 주): FMP ~500 호출 추가 ≈ 5~7분
  - Lambda 15분 타임아웃 안에 안정적. Memory: 1024MB 권장.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from typing import Any

import pyarrow as pa

# Lambda 가 /src 를 루트로 번들링하면 같은 레벨 패키지 import 가능하도록 경로 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common.fmp_client import FMPClient  # noqa: E402
from common.fundamentals import (  # noqa: E402
    DEFAULT_CACHE_MAX_AGE_DAYS,
    DEFAULT_CACHE_PREFIX as DEFAULT_FUNDAMENTALS_PREFIX,
    fetch_with_cache,
)
from common.models import Constituent  # noqa: E402
from common.s3_io import get_secret, read_parquet, write_text  # noqa: E402
from screening.constituents import arrow_to_constituents  # noqa: E402
from screening.pipeline import run_screening  # noqa: E402

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


# ---------- 설정 ----------


def _cfg() -> dict[str, Any]:
    bucket = os.environ.get("S3_BUCKET")
    secret_id = os.environ.get("FMP_SECRET_ID")
    if not bucket or not secret_id:
        raise RuntimeError("환경변수 S3_BUCKET 과 FMP_SECRET_ID 는 필수입니다")
    return {
        "bucket": bucket,
        "secret_id": secret_id,
        "constituents_prefix": os.environ.get("CONSTITUENTS_PREFIX", "metadata/constituents"),
        "ohlcv_prefix": os.environ.get("OHLCV_PREFIX", "ohlcv"),
        "fundamentals_prefix": os.environ.get("FUNDAMENTALS_PREFIX", DEFAULT_FUNDAMENTALS_PREFIX),
        "screening_prefix": os.environ.get("SCREENING_PREFIX", "screening"),
        "cache_max_age_days": int(os.environ.get("CACHE_MAX_AGE_DAYS", str(DEFAULT_CACHE_MAX_AGE_DAYS))),
    }


# ---------- 입력 로드 ----------


def _load_current_constituents(cfg: dict[str, Any]) -> list[Constituent]:
    key = f"{cfg['constituents_prefix']}/current.parquet"
    table = read_parquet(cfg["bucket"], key)
    if table is None:
        raise RuntimeError(
            f"s3://{cfg['bucket']}/{key} 없음 — update_constituents Lambda 를 먼저 실행하세요."
        )
    return [c for c in arrow_to_constituents(table) if c.is_current]


def _load_price_histories(
    cfg: dict[str, Any], symbols: list[str]
) -> dict[str, pa.Table | None]:
    histories: dict[str, pa.Table | None] = {}
    for symbol in symbols:
        key = f"{cfg['ohlcv_prefix']}/ticker={symbol}/data.parquet"
        histories[symbol] = read_parquet(cfg["bucket"], key)
    return histories


def _load_key_metrics(
    cfg: dict[str, Any], fmp: FMPClient, symbols: list[str]
) -> tuple[dict[str, dict[str, Any] | None], dict[str, str]]:
    """전 종목 key-metrics-ttm cache-aside. 반환: (응답 dict, 실패 dict).

    개별 종목 실패는 누락만 시키고 전체는 진행 (factors.py 가 None 처리).
    """
    metrics: dict[str, dict[str, Any] | None] = {}
    failures: dict[str, str] = {}
    for symbol in symbols:
        try:
            metrics[symbol] = fetch_with_cache(
                fmp,
                cfg["bucket"],
                symbol,
                prefix=cfg["fundamentals_prefix"],
                max_age_days=cfg["cache_max_age_days"],
            )
        except Exception as exc:  # noqa: BLE001 — 개별 실패가 전체 중단을 막아야 함
            failures[symbol] = f"{type(exc).__name__}: {exc}"
            metrics[symbol] = None
            logger.warning("%s key-metrics-ttm 실패: %s", symbol, exc)
    return metrics, failures


def _extract_market_caps(
    key_metrics: dict[str, dict[str, Any] | None],
) -> dict[str, float | None]:
    """key-metrics-ttm 응답의 marketCap 을 universe filter 입력으로 변환."""
    caps: dict[str, float | None] = {}
    for symbol, payload in key_metrics.items():
        if payload is None:
            caps[symbol] = None
            continue
        raw = payload.get("marketCap")
        try:
            caps[symbol] = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            caps[symbol] = None
    return caps


# ---------- 출력 저장 ----------


def _write_result(cfg: dict[str, Any], result_json: str, as_of_date: date) -> str:
    key = f"{cfg['screening_prefix']}/dt={as_of_date.isoformat()}/result.json"
    write_text(cfg["bucket"], key, result_json)
    return key


# ---------- 핸들러 ----------


def _parse_as_of_date(event: dict[str, Any] | None) -> date:
    if event and "as_of_date" in event:
        return datetime.strptime(event["as_of_date"], "%Y-%m-%d").date()
    return datetime.now(timezone.utc).date()


def lambda_handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    cfg = _cfg()
    as_of_date = _parse_as_of_date(event)
    run_id = (event or {}).get("run_id")

    logger.info(
        json.dumps({"stage": "start", "as_of_date": as_of_date.isoformat()})
    )
    start = time.time()

    # 1. 입력 로드
    fmp = FMPClient(api_key=get_secret(cfg["secret_id"]))
    constituents = _load_current_constituents(cfg)
    symbols = [c.symbol for c in constituents]
    logger.info(json.dumps({"stage": "loaded_constituents", "n_current": len(symbols)}))

    price_histories = _load_price_histories(cfg, symbols)
    n_with_history = sum(1 for v in price_histories.values() if v is not None)
    logger.info(json.dumps({"stage": "loaded_ohlcv", "n_with_history": n_with_history}))

    key_metrics, fmp_failures = _load_key_metrics(cfg, fmp, symbols)
    n_with_metrics = sum(1 for v in key_metrics.values() if v is not None)
    market_caps = _extract_market_caps(key_metrics)
    logger.info(
        json.dumps(
            {
                "stage": "loaded_fundamentals",
                "n_with_metrics": n_with_metrics,
                "n_fmp_failures": len(fmp_failures),
            }
        )
    )

    # 2. 스크리닝 실행 (순수 로직)
    result = run_screening(
        constituents=constituents,
        market_caps=market_caps,
        price_histories=price_histories,
        key_metrics_ttm=key_metrics,
        as_of_date=as_of_date,
        run_id=run_id,
    )

    # 3. S3 결과 저장 (Bull/Bear 다음 단계 입력)
    result_key = _write_result(cfg, result.model_dump_json(indent=2), as_of_date)
    elapsed = time.time() - start

    summary: dict[str, Any] = {
        "status": "ok",
        "as_of_date": as_of_date.isoformat(),
        "run_id": result.run_id,
        "universe_size": result.universe_size,
        "selected_count": len(result.selected),
        "result_s3_key": result_key,
        "n_fmp_failures": len(fmp_failures),
        "elapsed_seconds": round(elapsed, 1),
    }
    # Step Functions BullBearMap (docs/02-bull-bear.md §4.1) 가 ItemsPath 로 사용.
    # 20종목 × ~2KB ≈ ~40KB → SFN payload 256KB 한도 안. 로깅에는 noisy 라 별도 추가.
    summary["selected"] = [s.model_dump(mode="json") for s in result.selected]

    # 로그는 selected 제외 (CloudWatch 가독성)
    log_summary = {k: v for k, v in summary.items() if k != "selected"}
    logger.info(json.dumps({"stage": "completed", **log_summary}))

    # 디버깅 편의: 실패 상세는 반환값에 포함 (Step Functions 가 다음 state 로 전달)
    if fmp_failures:
        summary["fmp_failures"] = fmp_failures
    return summary
