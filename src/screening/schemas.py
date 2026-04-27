"""스크리닝 단계 출력 데이터 스키마.

설계 근거: docs/01-screening.md §2.2

이 모듈의 역할:
- 스크리닝 파이프라인의 모듈 간 인터페이스 (타입 계약)
- Pydantic 검증으로 모듈 경계에서 데이터 오염 차단
- Bull/Bear 단계로 전달되는 최종 출력(ScreeningResult)의 형태 정의

순수 데이터 스키마 — 네트워크/S3/AWS 호출 없음. LLM 사용 없음.
"""
from __future__ import annotations

from datetime import date
from typing import Self

from pydantic import BaseModel, Field, model_validator


class FactorScores(BaseModel):
    """단일 종목의 팩터 raw값과 섹터 중립화 z-score.

    raw 컴포넌트(momentum_12_1m 등)는 factors.py가 종목별로 채우고,
    z-score(momentum_z, value_z)는 normalize.py가 단면(cross-section) 단위로 채운다.
    """

    momentum_12_1m: float | None = None
    momentum_6m: float | None = None
    pe_ttm: float | None = None
    ev_ebitda: float | None = None
    fcf_yield: float | None = None
    momentum_z: float | None = None
    value_z: float | None = None


class PeerComparable(BaseModel):
    """같은 sub_sector 내 비교 종목의 멀티플.

    Bull/Bear 컨텍스트의 토큰 절감을 위해 스크리닝 단계에서 사전 조립.
    설계 근거: docs/01-screening.md 부록 A.
    """

    symbol: str
    pe_ttm: float | None = None
    ev_ebitda: float | None = None
    fcf_yield: float | None = None


class ScreenedStock(BaseModel):
    """선정된 단일 종목의 종합 결과.

    rank=1 이 최상위. peer_context 는 자기 자신을 제외한 같은 sub_sector
    상위 5개까지 (sub_sector 표본이 부족하면 그만큼만).
    """

    symbol: str
    company_name: str | None = None
    sector: str | None = None
    sub_sector: str | None = None
    rank: int = Field(ge=1)
    composite_score: float
    factors: FactorScores
    peer_context: list[PeerComparable] = Field(default_factory=list, max_length=5)
    data_quality_flags: list[str] = Field(default_factory=list)


class ScreeningResult(BaseModel):
    """스크리닝 단계의 최종 출력. Bull/Bear 단계의 입력원.

    검증 (docs/01-screening.md §2.3):
    - selected 길이 15~20 (CHARTER §3.2 포지션 수와 정합)
    - rank 가 selected 순서와 일치 (1, 2, 3, ...)
    - composite_score 내림차순
    """

    as_of_date: date
    universe_size: int = Field(ge=0)
    selected: list[ScreenedStock] = Field(min_length=15, max_length=20)
    factor_weights: dict[str, float]
    run_id: str

    @model_validator(mode="after")
    def _validate_ranking(self) -> Self:
        for idx, stock in enumerate(self.selected, start=1):
            if stock.rank != idx:
                raise ValueError(
                    f"rank 불일치: selected[{idx - 1}].rank={stock.rank}, expected {idx}"
                )
        scores = [s.composite_score for s in self.selected]
        for prev, curr in zip(scores, scores[1:]):
            if curr > prev:
                raise ValueError(
                    f"composite_score 는 내림차순이어야 함: {prev} 다음 {curr}"
                )
        return self
