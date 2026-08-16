"""4단계 포트폴리오 최적화 — 출력 스키마.

설계 근거: docs/04-optimizer.md §2.2

- `TargetPortfolio` — 주 1회 목표 비중 + 재현 lineage. 5단계 리밸런서의 입력
  (04 부록 A 계약). LLM 사용 없음 (CHARTER §3.3) — 모든 필드가 결정적 산출.
- `OptimizerBundle` — primary(옵션 C) + option_b_baseline(§6, §1.4.2 #3 비교용).
  5단계는 primary 만 소비.

저장: s3://{bucket}/portfolios/dt={D}/target.json
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "CovarianceParams",
    "TargetPortfolio",
    "OptimizerBundle",
]

# 최종(총자산 대비) 비중 제약 — CHARTER §3.2 + 04 §4.3
MIN_POSITION_WEIGHT = 0.03
MAX_POSITION_WEIGHT = 0.15
MAX_SECTOR_WEIGHT = 0.35


class CovarianceParams(BaseModel):
    """§4.2 하이브리드 Σ 구성 파라미터 (lineage 보존 — 재현성).

    v0.2 확정 제안값. 민감도는 구현 후 9주 재실행으로 재검증 (§9).
    """

    corr_window_days: int = Field(default=252, ge=60)
    shrinkage: float = Field(default=0.2, ge=0.0, le=1.0)
    var_floor: float = Field(default=0.0025, gt=0.0)


class TargetPortfolio(BaseModel):
    """주간 목표 비중 (§2.2).

    weights 는 *총자산 대비* 최종 비중 (현금 반영 후) — CHARTER 의 최소 3% /
    상한 15% / 섹터 35% 제약은 모두 이 최종 비중 기준 (§4.4~4.5).
    weights 합 + cash_weight = 1.
    """

    as_of_date: date
    method: Literal["max_sharpe"] = "max_sharpe"  # v1 고정 (§3.2)

    weights: dict[str, float] = Field(default_factory=dict)
    cash_weight: float = Field(ge=0.0, le=1.0)

    # 진단 (현금 0% 가정 아님 — 최종 비중 기준, 현금 기여 0)
    expected_portfolio_return: float
    portfolio_variance: float = Field(ge=0.0)

    # ---- 품질 게이트 lineage (§5) ----
    universe_size: int = Field(ge=0)
    excluded: dict[str, str] = Field(default_factory=dict)  # symbol → 사유
    n_candidates: int = Field(ge=0)

    # ---- 재현성 lineage ----
    pricing_config_hash: str = Field(min_length=8)
    covariance_params: CovarianceParams
    computed_at: datetime

    @model_validator(mode="after")
    def _validate_weights(self) -> Self:
        total = sum(self.weights.values())
        if not 0.99 <= total + self.cash_weight <= 1.01:
            raise ValueError(
                f"비중 합 위반: Σweights({total:.4f}) + cash({self.cash_weight:.4f}) ≠ 1"
            )
        for sym, w in self.weights.items():
            # 최종 비중 기준 3%~15% (§4.3~4.5). 부동소수 여유 1e-6
            if not (MIN_POSITION_WEIGHT - 1e-6 <= w <= MAX_POSITION_WEIGHT + 1e-6):
                raise ValueError(
                    f"{sym} 비중 {w:.4f} — [{MIN_POSITION_WEIGHT}, "
                    f"{MAX_POSITION_WEIGHT}] 범위 위반 (CHARTER §3.2)"
                )
        return self


class OptimizerBundle(BaseModel):
    """primary(옵션 C — 5단계 실제 입력) + 옵션 B baseline (§6, 비교 전용)."""

    primary: TargetPortfolio
    option_b_baseline: TargetPortfolio | None = None
