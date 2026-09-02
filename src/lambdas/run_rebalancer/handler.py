"""Lambda: run_rebalancer — 5단계 리밸런싱 (05 §7).

Step Functions `RunRebalancer` state (RunOptimizer 다음 — 실패 경로 포함) 또는
수동 invoke. **전용 컨테이너 이미지 Lambda** (infra/docker/rebalancer.Dockerfile).
매매 결정 LLM 호출 0 (CHARTER §3.3 — 룰 기반).

이벤트 입력 (선택):
  {
    "dt": "2026-09-07",   // 미지정 시 최신 screening 파티션 (이번 주 앵커)
    "force": false        // §4.3 멱등 가드 해제 — 로컬 리플레이 전용, 정기 실행 금지
  }

환경변수:
  S3_BUCKET          — 필수
  ACCOUNTS_PREFIX    — 기본 "accounts"
  PORTFOLIOS_PREFIX  — 기본 "portfolios"
  REBALANCE_BAND     — 기본 0.015 (§3.5)
  INITIAL_CASH       — 기본 10000 (CHARTER §2.3)
  LOG_LEVEL          — 기본 "INFO"

IAM: S3_BUCKET/* s3:GetObject·PutObject·ListBucket + CloudWatch Logs.
(FMP/Anthropic 시크릿 불필요 — S3 만 접근)
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

# 컨테이너/zip 공통: 패키지 루트를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from rebalancer.lambda_core import handle  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return handle(event or {}, context)
