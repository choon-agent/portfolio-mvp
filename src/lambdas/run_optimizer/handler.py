"""Lambda: run_optimizer — 4단계 포트폴리오 최적화 (04 §7).

Step Functions `RunOptimizer` state (ScenarioMap 다음) 또는 수동 invoke.
**컨테이너 이미지 Lambda** (infra/docker/optimizer.Dockerfile) — zip 아님.
LLM 호출 0 (CHARTER §3.3).

이벤트 입력 (선택):
  {
    "dt": "2026-08-10",              // 미지정 시 expected_returns 최신 파티션
    "covariance_params": { ... }     // CovarianceParams override (백테스트용)
  }

환경변수:
  S3_BUCKET           — 필수
  PORTFOLIOS_PREFIX   — 기본 "portfolios"
  LOG_LEVEL           — 기본 "INFO"

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

from optimizer.lambda_core import handle  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return handle(event or {}, context)
