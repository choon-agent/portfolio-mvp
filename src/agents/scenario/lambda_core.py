"""시나리오 Lambda 핸들러 공유 코어.

설계 근거: docs/03-scenario.md §6.1, §6.2, §9

이 모듈의 역할:
- Step Functions ScenarioMap 이 종목별로 invoke 하는 단일 호출의 코어 로직
- src/lambdas/agent_scenario/handler.py 는 thin wrapper

처리 흐름 (docs §6.1):
1. 입력 event 파싱 (screened_stock, as_of_date, run_id)
2. Bull/Bear 의견 2개 S3 로드 (결정적 키 재구성 — G4). 누락 시 skip (§9)
3. OHLCV S3 로드 + 분기 income cache-aside (ttm_eps 용)
4. build_context 로 ScenarioContext 조립 (ScenarioContextError → skip, §9)
5. scenario_input_hash → 캐시 hit/miss 판정 (docs §6.2)
   - hit: LLM 호출 생략, 저장 opinion 재사용 (cost=0)
   - miss: run_scenario_agent → opinion S3 저장
6. compute_expected_return (캐시 hit 에도 *항상* — 순수 함수, config 반영, §6.2)
7. ExpectedReturn + ScenarioContext S3 저장
8. 요약 dict 반환 (Step Functions 다음 state 전달)

캐시 정책 (docs §6.2): 캐시는 *LLM 호출* 만 대상 (비싼 부분). 가격 산식은
순수 함수라 hit 에도 재실행 — config 변경(§4.4 sensitivity) 시 ExpectedReturn
이 최신 반영. 키 = scenarios/dt=*/symbol=*.json 의 input_hash.

I/O 분리 (CLAUDE.md): caller(Anthropic)/fmp(FMP) 인자 주입 가능 — 테스트가 Fake.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Any

import pyarrow as pa
from pydantic import ValidationError

from common.fmp_client import FMPClient
from common.fundamentals import (
    DEFAULT_CACHE_MAX_AGE_DAYS,
    DEFAULT_INCOME_QUARTERLY_PREFIX,
    fetch_income_quarterly_with_cache,
)
from common.s3_io import get_secret, read_json, read_parquet, write_text
from screening.schemas import ScreenedStock

from agents.bull_bear.schemas import BullBearOpinion
from agents.scenario.agent import (
    ScenarioAgentError,
    run_scenario_agent,
    scenario_input_hash,
)
from agents.scenario.context_builder import ScenarioContextError, build_context
from agents.scenario.pricing import compute_expected_return
from agents.scenario.pricing_config import load_pricing_config
from agents.scenario.schemas import ScenarioOpinion

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

DEFAULT_OHLCV_PREFIX = "ohlcv"
DEFAULT_BULLBEAR_PREFIX = "agents/bullbear"
DEFAULT_SCENARIOS_PREFIX = "scenarios"
DEFAULT_EXPECTED_RETURNS_PREFIX = "expected_returns"
DEFAULT_SCENARIO_CONTEXTS_PREFIX = "scenario_contexts"


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
        "bullbear_prefix": os.environ.get("BULLBEAR_PREFIX", DEFAULT_BULLBEAR_PREFIX),
        "scenarios_prefix": os.environ.get("SCENARIOS_PREFIX", DEFAULT_SCENARIOS_PREFIX),
        "expected_returns_prefix": os.environ.get(
            "EXPECTED_RETURNS_PREFIX", DEFAULT_EXPECTED_RETURNS_PREFIX
        ),
        "scenario_contexts_prefix": os.environ.get(
            "SCENARIO_CONTEXTS_PREFIX", DEFAULT_SCENARIO_CONTEXTS_PREFIX
        ),
        "income_quarterly_prefix": os.environ.get(
            "INCOME_QUARTERLY_PREFIX", DEFAULT_INCOME_QUARTERLY_PREFIX
        ),
        "cache_max_age_days": int(
            os.environ.get("CACHE_MAX_AGE_DAYS", str(DEFAULT_CACHE_MAX_AGE_DAYS))
        ),
    }


# ---------- 입력 파싱 ----------


def _parse_event(event: dict[str, Any]) -> tuple[ScreenedStock, date, str]:
    try:
        screened = ScreenedStock.model_validate(event["screened_stock"])
        as_of_date = datetime.strptime(event["as_of_date"], "%Y-%m-%d").date()
        run_id = event["run_id"]
    except (KeyError, ValueError, TypeError) as exc:
        raise RuntimeError(f"입력 event 형식 오류: {exc}") from exc
    return screened, as_of_date, run_id


# ---------- S3 키 ----------


def _bullbear_key(prefix: str, as_of: date, symbol: str, stance: str) -> str:
    return f"{prefix}/dt={as_of.isoformat()}/symbol={symbol}/stance={stance}.json"


def _scenarios_key(prefix: str, as_of: date, symbol: str) -> str:
    return f"{prefix}/dt={as_of.isoformat()}/symbol={symbol}.json"


def _expected_returns_key(prefix: str, as_of: date, symbol: str) -> str:
    return f"{prefix}/dt={as_of.isoformat()}/symbol={symbol}.json"


def _context_key(prefix: str, as_of: date, symbol: str) -> str:
    return f"{prefix}/dt={as_of.isoformat()}/symbol={symbol}.json"


# ---------- 데이터 로딩 ----------


def _load_opinion(
    cfg: dict[str, Any], as_of: date, symbol: str, stance: str
) -> BullBearOpinion | None:
    """Bull/Bear 골든·운영 스냅샷 키에서 의견 로드. 누락/검증실패면 None."""
    key = _bullbear_key(cfg["bullbear_prefix"], as_of, symbol, stance)
    raw = read_json(cfg["bucket"], key)
    if not isinstance(raw, dict) or "opinion" not in raw:
        return None
    try:
        return BullBearOpinion.model_validate(raw["opinion"])
    except ValidationError:
        return None


def _load_ohlcv(cfg: dict[str, Any], symbol: str) -> pa.Table | None:
    key = f"{cfg['ohlcv_prefix']}/ticker={symbol}/data.parquet"
    return read_parquet(cfg["bucket"], key)


def _skip(reason: str, symbol: str, **extra: Any) -> dict[str, Any]:
    """종목 스킵 — Map 은 계속, 4단계는 expected_return 없는 종목으로 처리 (§9)."""
    logger.warning(json.dumps({"stage": "skipped", "reason": reason, "symbol": symbol}))
    return {"status": "skipped", "reason": reason, "symbol": symbol, **extra}


# ---------- 메인 ----------


def handle(
    event: dict[str, Any],
    context: Any,  # noqa: ARG001 — Lambda 시그니처 호환
    *,
    caller: Any | None = None,
    fmp: FMPClient | None = None,
) -> dict[str, Any]:
    """Lambda 시나리오 핸들러 코어 (docs §6.1).

    인자 주입 (단위 테스트):
        caller: Anthropic 호출 어댑터. None 이면 AnthropicSDKCaller + Secrets.
        fmp: FMP 클라이언트. None 이면 FMPClient + Secrets.
    """
    cfg = _cfg()
    screened, as_of_date, run_id = _parse_event(event)
    symbol = screened.symbol

    logger.info(
        json.dumps({"stage": "start", "symbol": symbol, "as_of_date": as_of_date.isoformat()})
    )

    # 1. Bull/Bear 의견 로드 — 누락 시 skip (§9)
    bull = _load_opinion(cfg, as_of_date, symbol, "bull")
    bear = _load_opinion(cfg, as_of_date, symbol, "bear")
    if bull is None or bear is None:
        return _skip(
            "bullbear_missing", symbol,
            bull_loaded=bull is not None, bear_loaded=bear is not None,
        )
    bullbear_s3_keys = {
        "bull": _bullbear_key(cfg["bullbear_prefix"], as_of_date, symbol, "bull"),
        "bear": _bullbear_key(cfg["bullbear_prefix"], as_of_date, symbol, "bear"),
    }

    # 2. 가격 데이터 로딩 (OHLCV + 분기 income — ttm_eps 용. cashflow 불필요)
    if fmp is None:
        fmp = FMPClient(api_key=get_secret(cfg["fmp_secret_id"]))
    ohlcv = _load_ohlcv(cfg, symbol)
    income_q = fetch_income_quarterly_with_cache(
        fmp, cfg["bucket"], symbol,
        prefix=cfg["income_quarterly_prefix"], max_age_days=cfg["cache_max_age_days"],
    )

    scenarios_key = _scenarios_key(cfg["scenarios_prefix"], as_of_date, symbol)
    er_key = _expected_returns_key(cfg["expected_returns_prefix"], as_of_date, symbol)
    ctx_key = _context_key(cfg["scenario_contexts_prefix"], as_of_date, symbol)

    # 3. ScenarioContext 조립 — 데이터 오류 시 skip (§9)
    try:
        ctx = build_context(
            screened, bull, bear,
            as_of_date=as_of_date, run_id=run_id,
            scenario_s3_key=scenarios_key, bullbear_s3_keys=bullbear_s3_keys,
            ohlcv=ohlcv, income_quarterly=income_q,
        )
    except ScenarioContextError as exc:
        return _skip("context_error", symbol, error=str(exc))

    input_hash = scenario_input_hash(ctx)

    # 4. 캐시 hit 판정 (동일 input_hash 면 LLM 호출 생략)
    cached = read_json(cfg["bucket"], scenarios_key)
    cache_hit = (
        isinstance(cached, dict)
        and cached.get("input_hash") == input_hash
        and "scenario_opinion" in cached
    )

    if cache_hit:
        opinion = ScenarioOpinion.model_validate(cached["scenario_opinion"])
        cache_status, cost_usd, attempts = "hit", 0.0, 0
    else:
        # 5. miss → LLM 호출
        if caller is None:
            from agents.bull_bear.anthropic_adapter import AnthropicSDKCaller

            caller = AnthropicSDKCaller(api_key=get_secret(cfg["anthropic_secret_id"]))
        try:
            result = run_scenario_agent(ctx, caller=caller)
        except ScenarioAgentError as exc:
            logger.error(
                json.dumps({
                    "stage": "agent_failed", "symbol": symbol,
                    "attempts": [{"stage": a.stage, "model": a.model, "error": a.error} for a in exc.attempts],
                })
            )
            raise
        opinion = result.opinion
        cache_status, cost_usd, attempts = "miss", result.total_cost_usd, len(result.attempts)
        # opinion S3 저장 (캐시 키)
        write_text(cfg["bucket"], scenarios_key, json.dumps({
            "scenario_opinion": opinion.model_dump(mode="json"),
            "attempts": [
                {"model": a.model, "stage": a.stage, "input_tokens": a.input_tokens,
                 "output_tokens": a.output_tokens, "cost_usd": a.cost_usd,
                 "succeeded": a.succeeded, "error": a.error}
                for a in result.attempts
            ],
            "input_hash": input_hash,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False))

    # 6. 가격 산정 — 캐시 hit 에도 항상 (순수 함수, config 반영, §6.2)
    pricing_cfg = load_pricing_config(
        env=os.environ, override=event.get("pricing_config_override")
    )
    expected_return = compute_expected_return(opinion, ctx, pricing_cfg)

    # 7. ExpectedReturn + ScenarioContext S3 저장
    write_text(cfg["bucket"], er_key, expected_return.model_dump_json())
    write_text(cfg["bucket"], ctx_key, ctx.model_dump_json())

    logger.info(
        json.dumps({
            "stage": "completed", "cache": cache_status, "symbol": symbol,
            "scenarios_s3_key": scenarios_key, "expected_returns_s3_key": er_key,
            "input_hash": input_hash, "attempts": attempts, "cost_usd": cost_usd,
            "expected_return": expected_return.expected_return,
            "data_quality_flags": expected_return.data_quality_flags,
        })
    )

    return {
        "status": "ok",
        "cache": cache_status,
        "symbol": symbol,
        "scenarios_s3_key": scenarios_key,
        "expected_returns_s3_key": er_key,
        "context_s3_key": ctx_key,
        "input_hash": input_hash,
        "cost_usd": cost_usd,
        "attempts": attempts,
        "expected_return": expected_return.expected_return,
        "data_quality_flags": expected_return.data_quality_flags,
    }
