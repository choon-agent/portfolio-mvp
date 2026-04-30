"""Bull/Bear Lambda 핸들러 공유 코어.

설계 근거: docs/02-bull-bear.md §4.1, §4.2, §6, §9 #7

이 모듈의 역할:
- Step Functions Map state 가 종목별로 invoke 하는 단일 호출의 코어 로직
- src/lambdas/agent_bullbear_{bull,bear}/handler.py 는 thin wrapper —
  stance ("bull" / "bear") 만 다르게 주입해 본 모듈의 handle() 호출

처리 흐름:
1. 입력 event 파싱 (screened_stock JSON, as_of_date, run_id, screening_s3_key)
2. OHLCV S3 로드 (기존 update_ohlcv Lambda 가 채운 캐시)
3. 분기 statements cache-aside (income/cash-flow — fundamentals.py)
4. context_builder.build_context 로 StockContext 조립
5. context_input_hash 산출 → S3 캐시 키와 함께 hit/miss 판정
6. hit + input_hash 동일: LLM 호출 생략, 저장된 opinion 그대로 반환
   miss: run_bullbear_agent → S3 저장 (opinion + context 별도)
7. 요약 dict 반환 (Step Functions 다음 state 전달)

캐시 정책 (docs §10 결정성 정책):
- 키: agents/bullbear/dt={yyyy-mm-dd}/symbol={SYM}/stance={bull|bear}.json
- payload 에 input_hash 포함 — 동일 키 + 동일 input_hash 면 hit (cost=0)
- 입력이 바뀌면 자연히 miss → LLM 재호출 (의도된 동작)

I/O 분리 (CLAUDE.md):
- caller (Anthropic), fmp (FMP) 는 인자 주입 가능 — 단위 테스트가 Fake 주입.
  None 이면 SDK adapter / FMP client 자체 인스턴스화 (Lambda 런타임 기본).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Any

import pyarrow as pa

from common.fmp_client import FMPClient
from common.fundamentals import (
    DEFAULT_CACHE_MAX_AGE_DAYS,
    DEFAULT_CASHFLOW_QUARTERLY_PREFIX,
    DEFAULT_INCOME_QUARTERLY_PREFIX,
    fetch_cashflow_quarterly_with_cache,
    fetch_income_quarterly_with_cache,
)
from common.s3_io import get_secret, read_json, read_parquet, write_text
from screening.schemas import ScreenedStock

from agents.bull_bear.agent import (
    AnthropicCaller,
    BullBearAgentError,
    context_input_hash,
    run_bullbear_agent,
)
from agents.bull_bear.context_builder import build_context

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

DEFAULT_OHLCV_PREFIX = "ohlcv"
DEFAULT_AGENTS_PREFIX = "agents/bullbear"


# ---------- 설정 ----------


def _cfg() -> dict[str, Any]:
    bucket = os.environ.get("S3_BUCKET")
    fmp_secret = os.environ.get("FMP_SECRET_ID")
    anthropic_secret = os.environ.get("ANTHROPIC_SECRET_ID")
    if not bucket or not fmp_secret or not anthropic_secret:
        raise RuntimeError(
            "환경변수 S3_BUCKET / FMP_SECRET_ID / ANTHROPIC_SECRET_ID 필수"
        )
    return {
        "bucket": bucket,
        "fmp_secret_id": fmp_secret,
        "anthropic_secret_id": anthropic_secret,
        "ohlcv_prefix": os.environ.get("OHLCV_PREFIX", DEFAULT_OHLCV_PREFIX),
        "agents_prefix": os.environ.get("AGENTS_PREFIX", DEFAULT_AGENTS_PREFIX),
        "income_quarterly_prefix": os.environ.get(
            "INCOME_QUARTERLY_PREFIX", DEFAULT_INCOME_QUARTERLY_PREFIX
        ),
        "cashflow_quarterly_prefix": os.environ.get(
            "CASHFLOW_QUARTERLY_PREFIX", DEFAULT_CASHFLOW_QUARTERLY_PREFIX
        ),
        "cache_max_age_days": int(
            os.environ.get("CACHE_MAX_AGE_DAYS", str(DEFAULT_CACHE_MAX_AGE_DAYS))
        ),
    }


# ---------- 입력 파싱 ----------


def _parse_event(event: dict[str, Any]) -> tuple[ScreenedStock, date, str, str]:
    try:
        screened = ScreenedStock.model_validate(event["screened_stock"])
        as_of_date = datetime.strptime(event["as_of_date"], "%Y-%m-%d").date()
        run_id = event["run_id"]
        screening_s3_key = event["screening_s3_key"]
    except (KeyError, ValueError, TypeError) as exc:
        raise RuntimeError(f"입력 event 형식 오류: {exc}") from exc
    return screened, as_of_date, run_id, screening_s3_key


# ---------- S3 키 ----------


def _opinion_key(prefix: str, as_of: date, symbol: str, stance: str) -> str:
    return f"{prefix}/dt={as_of.isoformat()}/symbol={symbol}/stance={stance}.json"


def _context_key(prefix: str, as_of: date, symbol: str) -> str:
    return f"{prefix}/dt={as_of.isoformat()}/symbol={symbol}/context.json"


# ---------- 캐시 ----------


def _try_cache_hit(
    cfg: dict[str, Any],
    *,
    as_of: date,
    symbol: str,
    stance: str,
    input_hash: str,
) -> dict[str, Any] | None:
    """동일 (key) + 동일 input_hash 면 hit 반환. 그 외 None."""
    key = _opinion_key(cfg["agents_prefix"], as_of, symbol, stance)
    cached = read_json(cfg["bucket"], key)
    if not isinstance(cached, dict):
        return None
    if cached.get("input_hash") != input_hash:
        return None
    return cached


# ---------- 데이터 로딩 ----------


def _load_ohlcv(cfg: dict[str, Any], symbol: str) -> pa.Table | None:
    key = f"{cfg['ohlcv_prefix']}/ticker={symbol}/data.parquet"
    return read_parquet(cfg["bucket"], key)


# ---------- 메인 ----------


def handle(
    event: dict[str, Any],
    context: Any,  # noqa: ARG001 — Lambda 시그니처 호환
    *,
    stance: str,
    caller: AnthropicCaller | None = None,
    fmp: FMPClient | None = None,
) -> dict[str, Any]:
    """Lambda Bull/Bear 핸들러 코어. stance 별 thin wrapper 가 호출.

    인자 주입 (단위 테스트):
        caller: Anthropic 호출 어댑터. None 이면 AnthropicSDKCaller +
                Secrets Manager 에서 API 키 조회.
        fmp: FMP 클라이언트. None 이면 FMPClient + Secrets Manager.
    """
    if stance not in ("bull", "bear"):
        raise ValueError(f"stance 는 'bull' 또는 'bear' — 받은 값: {stance!r}")

    cfg = _cfg()
    screened, as_of_date, run_id, screening_s3_key = _parse_event(event)

    logger.info(
        json.dumps(
            {
                "stage": "start",
                "stance": stance,
                "symbol": screened.symbol,
                "as_of_date": as_of_date.isoformat(),
            }
        )
    )

    # 1. 데이터 로딩 — OHLCV 는 S3, 분기 statements 는 cache-aside (FMP 가능)
    if fmp is None:
        fmp = FMPClient(api_key=get_secret(cfg["fmp_secret_id"]))
    ohlcv = _load_ohlcv(cfg, screened.symbol)
    income_q = fetch_income_quarterly_with_cache(
        fmp,
        cfg["bucket"],
        screened.symbol,
        prefix=cfg["income_quarterly_prefix"],
        max_age_days=cfg["cache_max_age_days"],
    )
    cashflow_q = fetch_cashflow_quarterly_with_cache(
        fmp,
        cfg["bucket"],
        screened.symbol,
        prefix=cfg["cashflow_quarterly_prefix"],
        max_age_days=cfg["cache_max_age_days"],
    )

    # 2. StockContext 조립 + input_hash
    ctx = build_context(
        screened,
        as_of_date=as_of_date,
        run_id=run_id,
        screening_s3_key=screening_s3_key,
        ohlcv=ohlcv,
        income_quarterly=income_q,
        cashflow_quarterly=cashflow_q,
    )
    input_hash = context_input_hash(ctx)

    output_key = _opinion_key(cfg["agents_prefix"], as_of_date, screened.symbol, stance)
    context_key = _context_key(cfg["agents_prefix"], as_of_date, screened.symbol)

    # 3. 캐시 hit 검사 — 동일 input_hash 면 LLM 호출 생략
    cached = _try_cache_hit(
        cfg,
        as_of=as_of_date,
        symbol=screened.symbol,
        stance=stance,
        input_hash=input_hash,
    )
    if cached is not None:
        logger.info(
            json.dumps(
                {
                    "stage": "cache_hit",
                    "stance": stance,
                    "symbol": screened.symbol,
                    "opinion_s3_key": output_key,
                    "input_hash": input_hash,
                }
            )
        )
        return {
            "status": "ok",
            "cache": "hit",
            "symbol": screened.symbol,
            "stance": stance,
            "opinion_s3_key": output_key,
            "context_s3_key": context_key,
            "input_hash": input_hash,
            "cost_usd": 0.0,
            "attempts": 0,
        }

    # 4. miss → LLM 호출
    if caller is None:
        from agents.bull_bear.anthropic_adapter import AnthropicSDKCaller

        caller = AnthropicSDKCaller(api_key=get_secret(cfg["anthropic_secret_id"]))

    try:
        result = run_bullbear_agent(ctx, stance, caller=caller)  # type: ignore[arg-type]
    except BullBearAgentError as exc:
        logger.error(
            json.dumps(
                {
                    "stage": "agent_failed",
                    "stance": stance,
                    "symbol": screened.symbol,
                    "attempts": [
                        {
                            "stage": a.stage,
                            "model": a.model,
                            "error": a.error,
                        }
                        for a in exc.attempts
                    ],
                }
            )
        )
        raise

    # 5. S3 저장 — opinion + context 별도. context 는 재현·디버깅용.
    payload = {
        "opinion": result.opinion.model_dump(mode="json"),
        "attempts": [
            {
                "model": a.model,
                "stage": a.stage,
                "input_tokens": a.input_tokens,
                "output_tokens": a.output_tokens,
                "cost_usd": a.cost_usd,
                "succeeded": a.succeeded,
                "error": a.error,
            }
            for a in result.attempts
        ],
        "input_hash": input_hash,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    write_text(cfg["bucket"], output_key, json.dumps(payload, ensure_ascii=False))
    write_text(
        cfg["bucket"],
        context_key,
        ctx.model_dump_json(),
    )

    logger.info(
        json.dumps(
            {
                "stage": "completed",
                "cache": "miss",
                "stance": stance,
                "symbol": screened.symbol,
                "opinion_s3_key": output_key,
                "input_hash": input_hash,
                "attempts": len(result.attempts),
                "cost_usd": result.total_cost_usd,
            }
        )
    )

    return {
        "status": "ok",
        "cache": "miss",
        "symbol": screened.symbol,
        "stance": stance,
        "opinion_s3_key": output_key,
        "context_s3_key": context_key,
        "input_hash": input_hash,
        "cost_usd": result.total_cost_usd,
        "attempts": len(result.attempts),
    }
