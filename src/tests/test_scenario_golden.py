"""시나리오 골든 케이스 회귀 검증 — 저장된 스냅샷이 schema·정책을 만족하는지.

설계 근거: docs/03-scenario.md §10.3, §11 #8

이 테스트는 LLM 호출을 *새로 하지 않는다*:
- `test_bullbear_snapshots_build_valid_context` — Bull/Bear 골든 스냅샷(이미 존재)
  이 ScenarioContext 로 조립되는지 (snapshot 포맷 drift 가드). CI 실행.
- `test_scenario_snapshot_*` (golden 마커) — scripts/run_scenario_golden.py 가
  저장한 시나리오 스냅샷 replay 검증. 스냅샷 없으면 0건 (false-positive 없음).

scenario 골든은 Bull/Bear 보다 강함 — 저장 ScenarioOpinion + ScenarioContext →
compute_expected_return 재실행 시 ExpectedReturn 이 결정적으로 정확 재현.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from agents.bull_bear.schemas import BullBearOpinion
from agents.scenario.pricing import compute_expected_return
from agents.scenario.schemas import ExpectedReturn, ScenarioContext, ScenarioOpinion

ROOT = Path(__file__).resolve().parent.parent.parent
BULLBEAR_DIR = ROOT / "tests" / "golden" / "bullbear"
SCENARIO_DIR = ROOT / "tests" / "golden" / "scenario"

GOLDEN_SYMBOLS = ["AAPL", "XOM", "NVDA", "JPM"]

# narrative 에 출현하면 안 되는 가격 추정 패턴 (hard rule #1 "No price estimation")
_PRICE_TARGET_RE = re.compile(
    r"price\s+target|target\s+price|per\s+share|fair\s+value\s+of\s+\$",
    re.IGNORECASE,
)


# ---------- Bull/Bear 스냅샷 → ScenarioContext (CI 실행, drift 가드) ----------


@pytest.mark.parametrize("symbol", GOLDEN_SYMBOLS)
def test_bullbear_snapshots_build_valid_context(symbol: str) -> None:
    """Bull/Bear 골든 스냅샷이 로드되어 유효한 ScenarioContext 를 만든다."""
    def _load(stance: str) -> BullBearOpinion:
        payload = json.loads((BULLBEAR_DIR / f"{symbol}_{stance}.json").read_text(encoding="utf-8"))
        return BullBearOpinion.model_validate(payload["opinion"])

    bull, bear = _load("bull"), _load("bear")
    ctx = ScenarioContext(
        symbol=symbol, as_of_date=date(2026, 4, 27),
        bull_opinion=bull, bear_opinion=bear,
        current_price=100.0, ttm_eps=5.0, peer_pe=[20.0, 25.0],
        return_52w_high=0.1, return_52w_low=-0.2,
        run_id="golden-2026-04-27",
        scenario_s3_key=f"scenarios/dt=2026-04-27/symbol={symbol}.json",
        bullbear_s3_keys={"bull": "kb", "bear": "kr"},
    )
    assert ctx.bull_opinion.stance == "bull"
    assert ctx.bear_opinion.stance == "bear"
    assert ctx.symbol == symbol == bull.symbol == bear.symbol


# ---------- 시나리오 스냅샷 replay (golden 마커) ----------


def _scenario_snapshots() -> list[Path]:
    if not SCENARIO_DIR.is_dir():
        return []
    return sorted(SCENARIO_DIR.glob("*.json"))


@pytest.mark.golden
@pytest.mark.parametrize("path", _scenario_snapshots(), ids=lambda p: p.stem)
def test_scenario_snapshot_schema_and_policy(path: Path) -> None:
    """저장 스냅샷의 ScenarioOpinion 이 schema·정책을 만족."""
    snap = json.loads(path.read_text(encoding="utf-8"))
    opinion = ScenarioOpinion.model_validate(snap["scenario_opinion"])

    # 구조: 3 라벨 unique + 확률 합 1.0 (model_validator 가 이미 보장하나 명시)
    labels = sorted(s.label for s in opinion.scenarios)
    assert labels == ["base", "bear", "bull"]
    assert abs(sum(s.probability for s in opinion.scenarios) - 1.0) <= 0.01

    # hard rule #1 — narrative 에 가격 타깃 미출현
    for s in opinion.scenarios:
        assert not _PRICE_TARGET_RE.search(s.narrative), (
            f"{path.stem}/{s.label} narrative 에 가격 추정: {s.narrative!r}"
        )


@pytest.mark.golden
@pytest.mark.parametrize("path", _scenario_snapshots(), ids=lambda p: p.stem)
def test_scenario_snapshot_expected_return_deterministic(path: Path) -> None:
    """저장 ScenarioOpinion + ScenarioContext → compute_expected_return 재실행 시
    저장 ExpectedReturn 정확 재현 (computed_at 제외) — M2 엔 없던 강한 회귀."""
    snap = json.loads(path.read_text(encoding="utf-8"))
    opinion = ScenarioOpinion.model_validate(snap["scenario_opinion"])
    ctx = ScenarioContext.model_validate(snap["scenario_context"])
    saved = ExpectedReturn.model_validate(snap["expected_return"])

    recomputed = compute_expected_return(opinion, ctx, saved.pricing_config)

    assert recomputed.expected_price == pytest.approx(saved.expected_price)
    assert recomputed.expected_return == pytest.approx(saved.expected_return)
    assert recomputed.variance == pytest.approx(saved.variance)
    assert recomputed.data_quality_flags == saved.data_quality_flags
    for label in ("bull", "base", "bear"):
        assert recomputed.scenario_prices[label] == pytest.approx(saved.scenario_prices[label])
