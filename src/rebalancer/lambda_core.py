"""5단계 rebalancer Lambda 공유 코어 — 계좌 루프·조립·저장 (05 §4, §7).

흐름 (계좌별 — primary / option_b, §3.6):
  1. dt 결정 (event["dt"] 우선, 없으면 최신 screening 파티션)
  2. 멱등 가드 (§4.3): accounts/{id}/dt={dt}/snapshot.json 존재 → 해당 계좌 skip
     (강제 재실행은 event["force"] — 로컬 스크립트 전용)
  3. state 로드 (없으면 $10,000 현금 초기화 — §4.1)
  4. target 로드: portfolios/dt={dt}/target.json 의 primary / option_b_baseline.
     파일·해당 키 부재 → 보유 유지 (G1, §5)
  5. 가격 로드 (G2/G4 — pricing) → compute_trades → apply_trades
  6. 성과: 직전 스냅샷 NAV 대비 주간 수익률 + SPY 동일 구간 (§6 — SPY 결측 허용)
  7. 쓰기 순서 고정 (§2.2): snapshot 먼저 → state.json 갱신

LLM 호출 0 (CHARTER §3.3). 실행 환경: 전용 컨테이너 Lambda (05 §7.2).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from common.s3_io import object_exists, read_json, write_text
from rebalancer.performance import weekly_return
from rebalancer.pricing import load_price_optional, load_prices
from rebalancer.schemas import (
    ACCOUNT_IDS,
    DEFAULT_INITIAL_CASH,
    DEFAULT_NO_TRADE_BAND,
    AccountId,
    AccountState,
    RebalanceSnapshot,
)
from rebalancer.trade_rules import account_nav, apply_trades, compute_trades

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

BENCHMARK_SYMBOL = "SPY"
_TARGET_KEY_BY_ACCOUNT: dict[AccountId, str] = {
    "primary": "primary",
    "option_b": "option_b_baseline",
}


def _config() -> dict[str, Any]:
    return {
        "bucket": os.environ["S3_BUCKET"],
        "accounts_prefix": os.environ.get("ACCOUNTS_PREFIX", "accounts"),
        "portfolios_prefix": os.environ.get("PORTFOLIOS_PREFIX", "portfolios"),
        "band": float(os.environ.get("REBALANCE_BAND", DEFAULT_NO_TRADE_BAND)),
        "initial_cash": float(os.environ.get("INITIAL_CASH", DEFAULT_INITIAL_CASH)),
    }


def _latest_screening_dt(bucket: str) -> str | None:
    """이번 주 실행 앵커 = 최신 screening 파티션 (1단계는 항상 존재)."""
    import boto3

    s3 = boto3.client("s3")
    dts: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="screening/dt=", Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            dts.add(cp["Prefix"].split("dt=")[1].rstrip("/"))
    return max(dts) if dts else None


def _load_target_weights(
    bucket: str, prefix: str, dt: str, account_id: AccountId
) -> tuple[dict[str, float] | None, str | None]:
    """(target_weights, target_dt). 부재 시 (None, None) = 보유 유지 (G1)."""
    bundle = read_json(bucket, f"{prefix}/dt={dt}/target.json")
    if bundle is None:
        return None, None
    portfolio = bundle.get(_TARGET_KEY_BY_ACCOUNT[account_id])
    if portfolio is None:
        return None, None
    return dict(portfolio["weights"]), dt


def _rebalance_account(
    cfg: dict[str, Any],
    account_id: AccountId,
    dt: str,
    now: datetime,
    *,
    force: bool = False,
) -> dict[str, Any]:
    bucket = cfg["bucket"]
    state_key = f"{cfg['accounts_prefix']}/{account_id}/state.json"
    snapshot_key = f"{cfg['accounts_prefix']}/{account_id}/dt={dt}/snapshot.json"
    as_of = datetime.strptime(dt, "%Y-%m-%d").date()

    if not force and object_exists(bucket, snapshot_key):        # §4.3 멱등 가드
        return {"account_id": account_id, "status": "skipped_existing_snapshot"}

    raw_state = read_json(bucket, state_key)
    if raw_state is None:                                        # §4.1 초기화
        state = AccountState(
            account_id=account_id, as_of_date=as_of,
            cash=cfg["initial_cash"], positions={}, inception_date=as_of,
        )
    else:
        state = AccountState.model_validate(raw_state)

    target, target_dt = _load_target_weights(
        bucket, cfg["portfolios_prefix"], dt, account_id
    )
    hold = target is None

    symbols = sorted(set(state.positions) | set(target or {}))
    prices = load_prices(bucket, symbols, as_of) if symbols else {}

    if hold:
        plan_trades, skipped = [], {}
    else:
        plan = compute_trades(state, target, prices, band=cfg["band"])
        plan_trades, skipped = plan.trades, plan.skipped_by_band
    post = apply_trades(state, plan_trades, as_of)
    nav = account_nav(post, prices)

    # ---- 성과 (§6) — 직전 스냅샷 NAV + SPY 동일 구간. 결측은 None ----
    ret, spy_ret = None, None
    prev_snap = read_json(
        bucket,
        f"{cfg['accounts_prefix']}/{account_id}/dt={state.as_of_date}/snapshot.json",
    )
    if prev_snap is not None and raw_state is not None:
        ret = weekly_return(nav, float(prev_snap["nav"]))
        spy_now = load_price_optional(bucket, BENCHMARK_SYMBOL, as_of)
        spy_prev = load_price_optional(bucket, BENCHMARK_SYMBOL, state.as_of_date)
        if spy_now is not None and spy_prev is not None:
            spy_ret = weekly_return(spy_now, spy_prev)

    snapshot = RebalanceSnapshot(
        account_id=account_id, as_of_date=as_of,
        pre_state=state, trades=plan_trades, post_state=post,
        nav=nav, prices_used={s: prices[s] for s in post.positions},
        weekly_return=ret, spy_weekly_return=spy_ret,
        target_dt=None if hold else datetime.strptime(target_dt, "%Y-%m-%d").date(),
        no_trade_band=cfg["band"], skipped_by_band=skipped,
        computed_at=now,
    )
    # §2.2 쓰기 순서 고정: snapshot(불변) → state(가변)
    write_text(bucket, snapshot_key, snapshot.model_dump_json())
    write_text(bucket, state_key, post.model_dump_json())

    return {
        "account_id": account_id,
        "status": "hold" if hold else "rebalanced",
        "snapshot_s3_key": snapshot_key,
        "n_trades": len(plan_trades),
        "buy_notional": round(sum(t.notional for t in plan_trades if t.side == "buy"), 2),
        "sell_notional": round(sum(t.notional for t in plan_trades if t.side == "sell"), 2),
        "n_skipped_by_band": len(skipped),
        "nav": round(nav, 2),
        "cash": round(post.cash, 2),
        "n_positions": len(post.positions),
        "weekly_return": ret,
        "spy_weekly_return": spy_ret,
    }


def handle(event: dict[str, Any], context: Any) -> dict[str, Any]:
    cfg = _config()
    now = datetime.now(timezone.utc)

    dt = event.get("dt") or _latest_screening_dt(cfg["bucket"])
    if dt is None:
        raise RuntimeError("screening 파티션 없음 — dt 결정 불가")

    accounts = [
        _rebalance_account(cfg, aid, dt, now, force=bool(event.get("force")))
        for aid in ACCOUNT_IDS
    ]
    summary = {"stage": "completed", "dt": dt, "accounts": accounts}
    logger.info(json.dumps(summary, ensure_ascii=False))
    return {"status": "ok", **summary}
