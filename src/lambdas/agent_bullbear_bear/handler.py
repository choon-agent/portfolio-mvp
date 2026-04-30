"""Lambda: agent_bullbear_bear.

Step Functions BullBearMap state 가 종목별 invoke. stance="bear" 고정.
나머지 모든 동작은 agent_bullbear_bull 과 동일 (lambda_core.handle 공유).

설계 근거: docs/02-bull-bear.md §4.2
환경변수 / 이벤트 입력은 agent_bullbear_bull/handler.py docstring 참조.
"""
from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from agents.bull_bear.lambda_core import handle  # noqa: E402


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return handle(event, context, stance="bear")
