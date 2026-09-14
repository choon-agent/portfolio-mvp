"""사전 백테스트 — config A/B × 포트폴리오 리플레이 + 구조 진단 (retro §0.8 A).

M3 말 재검토(~10-06)용. 저장된 산출물만 사용 — **LLM 0 / S3 쓰기 0** (읽기 전용).

Part A. 포트폴리오 시뮬: config 6종(primary / balanced / base_cap_10 / aggressive /
  bear_uncapped / option_b) × flag 정책 2종(exclude=운영 G1 / ignore) 별로.
  **ER 은 저장값이 아니라 현재 config 정의로 매주 재계산** (가격 산식 결정적 —
  v0.17 승격(08-04) 전 파티션은 primary 가 uncapped 였고 alternatives 정의도
  달라 저장값을 그대로 쓰면 config 시계열이 섞임). 08-04 이후 주차는 재계산
  primary 가 저장 primary 와 일치해야 하며 불일치 건수를 출력 (정합 검증).
  주차마다 4단계 optimizer 코드로 target 을 오프라인 산출 → 5단계 trade_rules 로
  $10,000 계좌를 리플레이. as_of 절단(look-ahead 차단)·동일 band·동일 체결 규칙.
Part B. 구조 진단 (주차 × config): 후보 중 bear=base=현재가 퇴화 비율 /
  ER 과 p_bull×return_52w_high 의 Spearman 순위상관 / 스크리닝 momentum_z 상위 5
  중 ER≤0 비율 (1↔3단계 방향 충돌) / 제외 사유 분포.

해석 원칙: 8주 표본 — 성과 순위 확정 금지, 구조 판정용 (후보 확보·퇴화·턴오버·게이트).

사용:
  .venv/bin/python scripts/run_config_backtest.py [--from 2026-07-14] [--to YYYY-MM-DD]
      [--exclude-dts 2026-08-10] [--configs primary,option_b,...]
      [--flag-policies exclude,ignore] [--band 0.015] [--output-dir retro_data/backtest]
출력: {output-dir}/{from}_{to}/summary.md + weekly.csv + targets.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import boto3  # noqa: E402
import pandas as pd  # noqa: E402

from agents.scenario.pricing import compute_expected_return  # noqa: E402
from agents.scenario.pricing_config import (  # noqa: E402
    ScenarioPricingConfig,
    alternative_configs,
)
from agents.scenario.schemas import (  # noqa: E402
    ExpectedReturn,
    ExpectedReturnsBundle,
    ScenarioContext,
    ScenarioOpinion,
)
from common.s3_io import read_json  # noqa: E402
from optimizer import data_loader, lambda_core  # noqa: E402
from optimizer.baseline import option_b_expected_return  # noqa: E402
from optimizer.data_loader import GateResult, SymbolData, config_hash  # noqa: E402
from optimizer.schemas import CovarianceParams  # noqa: E402
from rebalancer.performance import tracking_error, weekly_return  # noqa: E402
from rebalancer.pricing import load_price_optional, load_prices  # noqa: E402
from rebalancer.schemas import DEFAULT_INITIAL_CASH, AccountState  # noqa: E402
from rebalancer.trade_rules import account_nav, apply_trades, compute_trades  # noqa: E402

PRIMARY_CFG = ScenarioPricingConfig()                       # 현재 기본값 (v0.17)
CFGS: dict[str, ScenarioPricingConfig] = {"primary": PRIMARY_CFG, **alternative_configs(PRIMARY_CFG)}
ALT_CONFIGS = [k for k in CFGS if k != "primary"]
ALL_CONFIGS = ["primary", *ALT_CONFIGS, "option_b"]
MISMATCH: list[str] = []                                   # 재계산 vs 저장 primary 불일치
BENCHMARK = "SPY"


# ---------- 로드 ----------


@dataclass
class WeekData:
    dt: str
    as_of: date
    symbols: list[str]                                  # 스크리닝 selected
    momentum_z: dict[str, float]
    bundles: dict[str, ExpectedReturnsBundle] = field(default_factory=dict)
    ctx: dict[str, ScenarioContext] = field(default_factory=dict)
    opinion: dict[str, ScenarioOpinion] = field(default_factory=dict)
    returns: pd.DataFrame | None = None                # as_of 절단 로그수익률
    g3_excluded: dict[str, str] = field(default_factory=dict)


def _partitions(bucket: str) -> list[str]:
    s3 = boto3.client("s3")
    dts: set[str] = set()
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix="expected_returns/dt=", Delimiter="/"
    ):
        for cp in page.get("CommonPrefixes", []):
            dts.add(cp["Prefix"].split("dt=")[1].rstrip("/"))
    return sorted(dts)


def load_week(bucket: str, dt: str, params: CovarianceParams) -> WeekData:
    screening = read_json(bucket, f"screening/dt={dt}/result.json") or {}
    selected = screening.get("selected", [])
    wk = WeekData(
        dt=dt, as_of=datetime.strptime(dt, "%Y-%m-%d").date(),
        symbols=[s["symbol"] for s in selected],
        momentum_z={s["symbol"]: (s.get("factors") or {}).get("momentum_z") for s in selected},
    )
    for sym in wk.symbols:
        raw = read_json(bucket, f"expected_returns/dt={dt}/symbol={sym}.json")
        ctx_raw = read_json(bucket, f"scenario_contexts/dt={dt}/symbol={sym}.json")
        op_raw = read_json(bucket, f"scenarios/dt={dt}/symbol={sym}.json")
        if raw is None or ctx_raw is None or op_raw is None:
            continue                                      # G2 — ER/컨텍스트 결측
        wk.bundles[sym] = (
            ExpectedReturnsBundle.model_validate(raw) if "primary" in raw
            else ExpectedReturnsBundle(primary=ExpectedReturn.model_validate(raw))
        )
        wk.ctx[sym] = ScenarioContext.model_validate(ctx_raw)
        wk.opinion[sym] = ScenarioOpinion.model_validate(op_raw["scenario_opinion"])
    wk.returns, wk.g3_excluded = data_loader.load_return_matrix(
        bucket, sorted(wk.bundles), params, as_of=wk.as_of
    )
    return wk


_ER_CACHE: dict[tuple[str, str, str], ExpectedReturn] = {}


def config_er(wk: WeekData, sym: str, config: str) -> ExpectedReturn | None:
    """현재 config 정의로 재계산 (저장 ER 미사용 — 모듈 docstring)."""
    key = (wk.dt, sym, config)
    if key in _ER_CACHE:
        return _ER_CACHE[key]
    if config == "option_b":
        er = option_b_expected_return(wk.opinion[sym], wk.ctx[sym], PRIMARY_CFG)
    else:
        er = compute_expected_return(wk.opinion[sym], wk.ctx[sym], CFGS[config])
        if config == "primary":
            stored = wk.bundles[sym].primary
            if (stored.pricing_config == PRIMARY_CFG
                    and abs(stored.expected_return - er.expected_return) > 1e-9):
                MISMATCH.append(f"{wk.dt}/{sym}")
    _ER_CACHE[key] = er
    return er


# ---------- Part A: target 산출 + 리플레이 ----------


def build_target(
    wk: WeekData, config: str, flag_policy: str, params: CovarianceParams, now: datetime
) -> tuple[dict[str, float] | None, dict]:
    """(weights|None=보유 유지, 진단 dict). optimizer._build_portfolio 재사용."""
    gate = GateResult(dt=wk.dt, universe_size=len(wk.symbols))
    ers: dict[str, ExpectedReturn] = {}
    for sym in wk.symbols:
        if sym not in wk.bundles:
            gate.excluded[sym] = "expected_return_missing"
            continue
        er = config_er(wk, sym, config)
        if er is None:
            gate.excluded[sym] = "config_missing"
            continue
        if flag_policy == "exclude" and er.data_quality_flags:
            gate.excluded[sym] = f"data_quality_flags: {er.data_quality_flags[0]}"
            continue
        ers[sym] = er
        gate.passed[sym] = SymbolData(primary=er, ctx=wk.ctx[sym], opinion=wk.opinion[sym])
    diag = {"n_passed": len(gate.passed), "n_flagged": sum(
        1 for s in wk.symbols if s in wk.bundles and (config_er(wk, s, config) or ExpectedReturn.model_construct(data_quality_flags=[])).data_quality_flags)}
    if not gate.passed:                                   # G5 → 보유 유지
        return None, {**diag, "n_candidates": 0, "hold": True}
    gate.pricing_config_hash = config_hash(
        next(iter(ers.values())).pricing_config.model_dump(mode="json")
    )
    tp = lambda_core._build_portfolio(
        er_by_symbol={s: e.expected_return for s, e in ers.items()},
        gate=gate, returns=wk.returns, extra_excluded=wk.g3_excluded,
        params=params, now=now,
    )
    return dict(tp.weights), {
        **diag, "n_candidates": tp.n_candidates, "cash_weight": tp.cash_weight,
        "n_positions": len(tp.weights), "portfolio_er": tp.expected_portfolio_return,
        "portfolio_sd": tp.portfolio_variance ** 0.5, "hold": False,
        "excluded": tp.excluded,
    }


# ---------- Part B: 구조 진단 ----------


def structure_diag(wk: WeekData, config: str, flag_policy: str) -> dict:
    rows = []
    for sym in wk.symbols:
        if sym not in wk.bundles:
            continue
        er = config_er(wk, sym, config)
        if er is None or (flag_policy == "exclude" and er.data_quality_flags):
            continue
        ctx = wk.ctx[sym]
        sp = er.scenario_prices
        cp = ctx.current_price
        p_bull = next(s.probability for s in wk.opinion[sym].scenarios if s.label == "bull")
        rows.append({
            "sym": sym, "er": er.expected_return, "mom_z": wk.momentum_z.get(sym),
            "degenerate": abs(sp["bear"] - cp) < 1e-6 and abs(sp["base"] - cp) < 1e-6,
            "pbull_x_52wh": p_bull * (ctx.return_52w_high or 0.0),
        })
    if not rows:
        return {"degen_frac": None, "spearman_er_vs_pbull52w": None, "mom_top5_er_nonpos": None}
    df = pd.DataFrame(rows)
    cand = df[df.er > 0]
    top5 = df.dropna(subset=["mom_z"]).nlargest(5, "mom_z")
    return {
        "degen_frac": round(float(cand.degenerate.mean()), 3) if len(cand) else None,
        "spearman_er_vs_pbull52w": round(float(df.er.corr(df.pbull_x_52wh, method="spearman")), 3)
        if len(df) > 2 else None,
        "mom_top5_er_nonpos": round(float((top5.er <= 0).mean()), 2) if len(top5) else None,
    }


# ---------- 실행 ----------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", default=os.environ.get("S3_BUCKET", "portfolio-mvp-data-s3"))
    ap.add_argument("--from", dest="dt_from", default="2026-07-14")
    ap.add_argument("--to", dest="dt_to", default="9999-12-31")
    ap.add_argument("--exclude-dts", default="2026-08-10")
    ap.add_argument("--configs", default=",".join(ALL_CONFIGS))
    ap.add_argument("--flag-policies", default="exclude,ignore")
    ap.add_argument("--band", type=float, default=0.015)
    ap.add_argument("--output-dir", default="retro_data/backtest")
    args = ap.parse_args()
    os.environ.setdefault("S3_BUCKET", args.bucket)

    excl = {d for d in args.exclude_dts.split(",") if d}
    dts = [d for d in _partitions(args.bucket) if args.dt_from <= d <= args.dt_to and d not in excl]
    configs = args.configs.split(",")
    policies = args.flag_policies.split(",")
    params = CovarianceParams()
    now = datetime.now(timezone.utc)
    print(f"주차 {len(dts)}: {dts}\nconfig {configs} × policy {policies}")

    weeks = [load_week(args.bucket, dt, params) for dt in dts]
    keys = [(c, p) for c in configs for p in policies]
    states = {k: None for k in keys}
    prev_nav: dict[tuple[str, str], float] = {}
    spy_prev: float | None = None
    weekly_rows: list[dict] = []
    targets: dict[str, dict] = {}
    spy_series: list[float] = []

    for wk in weeks:
        print(f"\n===== dt={wk.dt} (ER {len(wk.bundles)}/{len(wk.symbols)}, G3 {len(wk.g3_excluded)})")
        spy_now = load_price_optional(args.bucket, BENCHMARK, wk.as_of)
        spy_r = weekly_return(spy_now, spy_prev) if spy_now and spy_prev else None
        if spy_r is not None:
            spy_series.append(spy_r)
        for c, p in keys:
            weights, diag = build_target(wk, c, p, params, now)
            targets[f"{wk.dt}/{c}/{p}"] = weights
            st = states[(c, p)] or AccountState(
                account_id="primary", as_of_date=wk.as_of, cash=DEFAULT_INITIAL_CASH,
                positions={}, inception_date=wk.as_of,
            )
            symbols = sorted(set(st.positions) | set(weights or {}))
            prices = load_prices(args.bucket, symbols, wk.as_of) if symbols else {}
            nav_pre = account_nav(st, prices)
            if weights is None:
                trades, skipped = [], {}
            else:
                plan = compute_trades(st, weights, prices, band=args.band)
                trades, skipped = plan.trades, plan.skipped_by_band
            post = apply_trades(st, trades, wk.as_of)
            nav = account_nav(post, prices)
            r = weekly_return(nav, prev_nav[(c, p)]) if (c, p) in prev_nav else None
            turnover = (max(sum(t.notional for t in trades if t.side == "sell"),
                            sum(t.notional for t in trades if t.side == "buy")) / nav_pre
                        if nav_pre else 0.0)
            states[(c, p)] = post
            prev_nav[(c, p)] = nav
            row = {"dt": wk.dt, "config": c, "policy": p, "nav": round(nav, 2),
                   "r": r, "spy_r": spy_r, "turnover": round(turnover, 4),
                   "n_trades": len(trades), "n_skipped_band": len(skipped),
                   **{k: v for k, v in diag.items() if k != "excluded"},
                   **structure_diag(wk, c, p)}
            weekly_rows.append(row)
            print(f"  {c:13}/{p:7} nav ${nav:>9,.2f} r={'—' if r is None else f'{r:+.2%}'} "
                  f"cand {diag.get('n_candidates', 0):2} cash {diag.get('cash_weight', 1.0):.0%} "
                  f"pos {diag.get('n_positions', 0):2} to {turnover:.0%} "
                  f"degen {row['degen_frac']} ρ {row['spearman_er_vs_pbull52w']} "
                  f"momtop5≤0 {row['mom_top5_er_nonpos']}{' HOLD' if diag.get('hold') else ''}")
        spy_prev = spy_now

    # ---------- 요약 ----------
    df = pd.DataFrame(weekly_rows)
    out = ROOT / args.output_dir / f"{dts[0]}_{dts[-1]}"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "weekly.csv", index=False)
    (out / "targets.json").write_text(json.dumps(targets, indent=1))

    lines = [f"# 사전 백테스트 — {dts[0]} ~ {dts[-1]} ({len(dts)}주, band {args.band}, LLM 0)", "",
             f"ER 재계산 정합: 저장 primary(v0.17 config 주차)와 불일치 {len(MISMATCH)}건"
             + (f" — {MISMATCH[:5]}" if MISMATCH else ""), "",
             f"SPY 누적 {(pd.Series(spy_series) + 1).prod() - 1:+.2%} (주간 {len(spy_series)}개)", "",
             "| config | policy | 누적수익 | 주간σ | TE(연) | 평균 턴오버 | 평균 현금 | 평균 종목 | 평균 후보 | hold주 | 퇴화비율 | ρ(ER, p_bull×52wH) | mom top5 ER≤0 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for (c, p), g in df.groupby(["config", "policy"], sort=False):
        rs = [x for x in g.r if x is not None and not pd.isna(x)]
        spy = [x for x in g.spy_r if x is not None and not pd.isna(x)]
        te = tracking_error(rs, spy) if len(rs) == len(spy) else None
        cum = g.nav.iloc[-1] / DEFAULT_INITIAL_CASH - 1
        lines.append(
            f"| {c} | {p} | {cum:+.2%} | {statistics.pstdev(rs) if len(rs) > 1 else 0:.2%} | "
            f"{'—' if te is None else f'{te:.1%}'} | {g.turnover.mean():.0%} | "
            f"{g.cash_weight.fillna(1.0).mean():.0%} | {g.n_positions.fillna(0).mean():.1f} | "
            f"{g.n_candidates.fillna(0).mean():.1f} | {int(g.hold.sum())} | "
            f"{g.degen_frac.mean():.2f} | {g.spearman_er_vs_pbull52w.mean():.2f} | "
            f"{g.mom_top5_er_nonpos.mean():.2f} |")
    lines += ["", "> 8주 표본 — 성과 순위 확정 금지. 구조 판정(후보 확보·퇴화·턴오버·게이트)용.",
              f"> weekly.csv / targets.json: {out}"]
    (out / "summary.md").write_text("\n".join(lines))
    print("\n" + "\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
