"""Lambda: agent_bullbear_bull.

Step Functions BullBearMap state 가 종목별 invoke. stance="bull" 고정.
실제 로직은 agents.bull_bear.lambda_core.handle 이 책임 — 본 파일은 stance
주입만 하는 thin wrapper (bear 측과 동일 코어 공유, 모델/프롬프트만 분기).

설계 근거: docs/02-bull-bear.md §4.2

이벤트 입력 (Step Functions Map state per-item):
  {
    "screened_stock": {... ScreenedStock JSON ...},
    "as_of_date": "2026-05-04",
    "run_id": "2026-05-04T00:00:00Z",
    "screening_s3_key": "screening/dt=2026-05-04/result.json"
  }

환경변수 (lambda_core._cfg 참조):
  S3_BUCKET             — 필수
  FMP_SECRET_ID         — 필수 (분기 statements cache-aside)
  ANTHROPIC_SECRET_ID   — 필수 (LLM 호출)
  OHLCV_PREFIX          — 기본 "ohlcv"
  AGENTS_PREFIX         — 기본 "agents/bullbear"
  INCOME_QUARTERLY_PREFIX / CASHFLOW_QUARTERLY_PREFIX
  CACHE_MAX_AGE_DAYS    — 기본 90
"""
from __future__ import annotations

import os
import sys
from typing import Any

# Lambda 가 src/ 를 루트로 번들링하므로 패키지 import 경로 보강
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from agents.bull_bear.lambda_core import handle  # noqa: E402


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return handle(event, context, stance="bull")
