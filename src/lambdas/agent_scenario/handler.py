"""Lambda: agent_scenario.

Step Functions ScenarioMap state 가 종목별 invoke. Bull/Bear 와 달리 stance
분리 없음 — 단일 함수. 실제 로직은 agents.scenario.lambda_core.handle 이 책임,
본 파일은 thin wrapper.

설계 근거: docs/03-scenario.md §6.1, §6.2

이벤트 입력 (Step Functions ScenarioMap per-item, docs §6.1 ItemSelector):
  {
    "screened_stock": {... ScreenedStock JSON ...},
    "as_of_date": "2026-05-04",
    "run_id": "2026-05-04T00:00:00Z",
    "pricing_config_override": {...}   # 선택 — dry-run/백테스트 (§4.3)
  }

환경변수 (lambda_core._cfg 참조):
  S3_BUCKET             — 필수
  FMP_SECRET_ID         — 필수 (분기 income cache-aside, ttm_eps)
  ANTHROPIC_SECRET_ID   — 필수 (LLM 호출)
  OHLCV_PREFIX          — 기본 "ohlcv"
  BULLBEAR_PREFIX       — 기본 "agents/bullbear" (입력 의견 로드)
  SCENARIOS_PREFIX / EXPECTED_RETURNS_PREFIX / SCENARIO_CONTEXTS_PREFIX
  INCOME_QUARTERLY_PREFIX / CACHE_MAX_AGE_DAYS
  SCENARIO_* (pricing config override — §4.3)
"""
from __future__ import annotations

import os
import sys
from typing import Any

# Lambda 가 src/ 를 루트로 번들링하므로 패키지 import 경로 보강
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from agents.scenario.lambda_core import handle  # noqa: E402


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return handle(event, context)
