"""S3 로드 + 품질 게이트 (04 §2.1, §5) — I/O 격리 계층.

게이트 (제외 사유는 excluded 로 lineage 보존):
  G1 flag        — data_quality_flags 비어있지 않음
  G2 ER 결측     — 스크리닝 selected 인데 expected_returns 없음 (시나리오 스킵)
  G3 상관 결측   — OHLCV < MIN_OHLCV_DAYS 거래일
  G4 config 불일치 — 전 종목 pricing_config 동일 검증, 위반 시 런 전체 실패
                    (부분 배포 등 조용한 오염 방지 — epsDiluted 교훈)
  G5 통과 종목 0   — G1~G2 후 남는 종목이 없으면 런 전체 실패. "후보 0 = 전량
                    매도"(§4.5) 는 ER 이 *존재하되 전부 ≤0* 일 때만 유효 — ER 이
                    아예 없는 상류 실패(2026-09-07 SDK 사고: Bull/Bear 40/40 실패
                    → 3단계 전부 skip) 를 전량 매도 신호로 오역하는 것 차단.
                    실패 → 5단계 보유 유지 (05 §5 G1)

ER ≤ 0 은 게이트가 아니라 후보 규칙 (§4.5) — lambda_core 가 처리하되
excluded 에 "er_not_positive" 로 함께 기록 (회고 lineage).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import boto3
import pandas as pd

from agents.scenario.schemas import (
    ExpectedReturn,
    ExpectedReturnsBundle,
    ScenarioContext,
    ScenarioOpinion,
)
from common.s3_io import read_json, read_parquet
from optimizer.covariance import log_returns
from optimizer.schemas import CovarianceParams

__all__ = [
    "GateResult", "ConfigMismatchError", "NoPassedSymbolsError",
    "load_gated_universe", "load_return_matrix", "config_hash",
]

MIN_OHLCV_DAYS = 60

_s3 = boto3.client("s3")


@dataclass
class SymbolData:
    primary: ExpectedReturn
    ctx: ScenarioContext
    opinion: ScenarioOpinion


@dataclass
class GateResult:
    dt: str
    universe_size: int                      # 스크리닝 selected 수
    passed: dict[str, SymbolData] = field(default_factory=dict)
    excluded: dict[str, str] = field(default_factory=dict)
    pricing_config_hash: str = ""


class ConfigMismatchError(RuntimeError):
    """G4 — 같은 dt 안에서 pricing_config 가 종목마다 다름 (런 전체 실패)."""


class NoPassedSymbolsError(RuntimeError):
    """G5 — 게이트 통과 종목 0 (상류 실패 신호 — 전량 매도 target 발행 차단)."""


def config_hash(config_dump: dict) -> str:
    return hashlib.sha256(
        json.dumps(config_dump, sort_keys=True).encode()
    ).hexdigest()[:16]


def _screening_symbols(bucket: str, dt: str) -> list[str]:
    result = read_json(bucket, f"screening/dt={dt}/result.json")
    if result is None:
        return []
    return [s["symbol"] for s in result.get("selected", [])]


def latest_dt(bucket: str, prefix: str = "expected_returns") -> str | None:
    """expected_returns/dt=* 중 최신 파티션 (event 에 dt 미지정 시)."""
    dts: set[str] = set()
    paginator = _s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/dt=", Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            dts.add(cp["Prefix"].split("dt=")[1].rstrip("/"))
    return max(dts) if dts else None


def load_gated_universe(bucket: str, dt: str) -> GateResult:
    """expected_returns + contexts + scenarios 로드 → G1/G2/G4 게이트."""
    symbols = _screening_symbols(bucket, dt)
    result = GateResult(dt=dt, universe_size=len(symbols))
    hashes: set[str] = set()

    for sym in sorted(symbols):
        raw = read_json(bucket, f"expected_returns/dt={dt}/symbol={sym}.json")
        if raw is None:
            result.excluded[sym] = "expected_return_missing"  # G2
            continue
        primary = (
            ExpectedReturnsBundle.model_validate(raw).primary
            if "primary" in raw
            else ExpectedReturn.model_validate(raw)
        )
        if primary.data_quality_flags:                        # G1
            result.excluded[sym] = f"data_quality_flags: {primary.data_quality_flags[0]}"
            continue
        ctx_raw = read_json(bucket, f"scenario_contexts/dt={dt}/symbol={sym}.json")
        saved = read_json(bucket, f"scenarios/dt={dt}/symbol={sym}.json")
        if ctx_raw is None or saved is None:
            result.excluded[sym] = "context_or_opinion_missing"
            continue
        result.passed[sym] = SymbolData(
            primary=primary,
            ctx=ScenarioContext.model_validate(ctx_raw),
            opinion=ScenarioOpinion.model_validate(saved["scenario_opinion"]),
        )
        hashes.add(config_hash(primary.pricing_config.model_dump(mode="json")))

    if len(hashes) > 1:                                       # G4
        raise ConfigMismatchError(
            f"dt={dt} pricing_config 불일치 — {len(hashes)}종의 config 혼재"
        )
    if not result.passed:                                     # G5
        raise NoPassedSymbolsError(
            f"dt={dt} 게이트 통과 종목 0 (universe {result.universe_size}, "
            f"excluded {dict(sorted(result.excluded.items()))}) — 상류(2~3단계) "
            "실패로 간주, target 미발행 (5단계 보유 유지)"
        )
    result.pricing_config_hash = hashes.pop()
    return result


def load_return_matrix(
    bucket: str, symbols: list[str], params: CovarianceParams
) -> tuple[pd.DataFrame, dict[str, str]]:
    """OHLCV → (일자 × 종목) 로그수익률 행렬 + G3 제외 목록."""
    series: dict[str, pd.Series] = {}
    excluded: dict[str, str] = {}
    for sym in symbols:
        table = read_parquet(bucket, f"ohlcv/ticker={sym}/data.parquet")
        if table is None:
            excluded[sym] = "insufficient_ohlcv"              # G3
            continue
        adj = table.to_pandas().set_index("date")["adj_close"]
        r = log_returns(adj, params.corr_window_days)
        if len(r) < MIN_OHLCV_DAYS:
            excluded[sym] = "insufficient_ohlcv"              # G3
            continue
        series[sym] = r
    return pd.DataFrame(series).dropna(), excluded
