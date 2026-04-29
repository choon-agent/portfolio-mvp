"""Bull/Bear 에이전트 단계의 입출력 데이터 스키마.

설계 근거: docs/02-bull-bear.md §2.1, §2.2

이 모듈의 역할:
- Bull/Bear 에이전트의 입력(StockContext) 과 출력(BullBearOpinion) 형태 고정
- 평탄화된 1-depth 구조 
— ScreenedStock 임베드 대신 명시 필드로 전개
  ("LLM 이 보는 데이터의 정확한 형태"를 한 타입에 박제, docs §2.1)
- Pydantic 검증으로 LLM JSON 출력의 형식 위반·결측을 모듈 경계에서 차단

순수 데이터 스키마 — 네트워크/S3/AWS/LLM 호출 없음.

PeerComparable 은 screening 단계에서 사전 조립되어 그대로 전달되므로
(docs §2.1 출처 매핑, 부록 B) screening.schemas 에서 재사용한다 
— 동일 타입 유지가 매퍼의 1:1 평탄화 가드 (test_bullbear_schemas) 의 전제.

LLM 프롬프트 노출 정책 (docs §2.1.2):
- 프롬프트로 들어감: identity, screening signals, peer_context, price_summary,
  fundamentals
- 프롬프트로 안 감 (audit/재현 전용): run_id, screening_s3_key,
  data_quality_flags
이 분리는 schema 가 아니라 context_builder.to_prompt_markdown() 가 강제한다 
— schema 레벨에서는 모든 필드가 한 객체에 함께 보존돼야 S3 저장본이 ScreeningResult 와 1:1 lineage 를 유지할 수 있다.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from screening.schemas import PeerComparable

__all__ = [
    "PeerComparable",  # 재export — Bull/Bear 사용자가 한 모듈에서 모두 import 가능
    "QuarterlyFigures",
    "PriceSummary",
    "FundamentalsTimeseries",
    "StockContext",
    "Argument",
    "BullBearOpinion",
]


# ---------- 시계열 컨텍스트 (Bull/Bear context_builder 가 캐시에서 조립) ----------


class QuarterlyFigures(BaseModel):
    """단일 회계분기의 요약 지표.

    FundamentalsTimeseries.quarters 의 element. 직전 4분기 시계열용.
    분기 식별은 회계기간 종료일(period_end) 로 한다 — 회계연도/분기 번호는
    회사마다 다르므로 정렬·표시는 종료일 기준이 충돌이 적다.
    """

    period_end: date
    revenue: float | None = None
    eps_diluted: float | None = None
    fcf: float | None = None


class PriceSummary(BaseModel):
    """직전 가격 흐름의 요약 5종.

    OHLCV 캐시에서 도출. 결측은 None — context_builder 가 데이터 부족 시
    채우지 않는다 (docs §7 기조: 결측은 결측으로).
    """

    return_1y: float | None = None       # 직전 252영업일 수익률
    return_6m: float | None = None       # 직전 126영업일 수익률
    pct_from_52w_high: float | None = None  # (close - 52w_high) / 52w_high (≤ 0)
    pct_from_52w_low: float | None = None   # (close - 52w_low) / 52w_low (≥ 0)
    beta_1y: float | None = None         # SPY 대비 1Y 회귀 베타


class FundamentalsTimeseries(BaseModel):
    """직전 4분기 시계열 + 5Y CAGR.

    quarters 는 0~4개 — 발표 시즌 직전이거나 신규 상장이면 부족할 수 있다.
    schema 는 정렬을 강제하지 않으나 context_builder 는 period_end desc
    (최신 우선) 로 채워 LLM 프롬프트의 가독성을 통일한다.
    """

    quarters: list[QuarterlyFigures] = Field(default_factory=list, max_length=4)
    revenue_cagr_5y: float | None = None
    eps_cagr_5y: float | None = None
    fcf_cagr_5y: float | None = None


# ---------- 입력 (LLM 에 전달) ----------


class StockContext(BaseModel):
    """Bull/Bear 에이전트의 단일 호출 입력.

    평탄화 1-depth 구조 — docs §2.1 의 결정. 핵심 이점:
    - 스크리닝-Bull/Bear 결합도 ↓ (매퍼에서 이름·구조 차이 흡수)
    - 입력 토큰 효율 ↑ (불필요 필드 사전 배제)
    - "LLM 이 보는 형태" 가 타입 한 곳에 박제 → 프롬프트 회귀 테스트 단순

    필드 그룹:
    1. Identity         — symbol/이름/sector + as_of_date
    2. Screening signals — composite_score 와 z-score, TTM 멀티플 (docs §3.2 에
                           프롬프트로 명시 노출, 단 score 자체는 매수/매도 근거가
                           아님을 시스템 프롬프트가 강제)
    3. Peer context     — sub_sector → sector 폴백으로 사전 조립된 PeerComparable
    4. Time series      — price_summary, fundamentals (context_builder 책임)
    5. Lineage (audit)  — run_id, screening_s3_key, data_quality_flags
                          → schema 에는 보존하지만 LLM 프롬프트로는 미노출
                            (직렬화 컨벤션은 context_builder.to_prompt_markdown)

    cross-field 검증을 두지 않은 이유: 결측은 결측 그대로 보존하고 (docs §7),
    호출 가치 판단(컨텍스트 6K tok 초과/심각한 결측 시 호출 스킵)은 상위
    context_builder 에 위임 — schema 는 형태만 책임.
    """

    # 1. Identity
    symbol: str
    company_name: str | None = None
    sector: str | None = None
    sub_sector: str | None = None
    as_of_date: date

    # 2. Screening signals (from ScreenedStock — LLM 노출)
    composite_score: float
    momentum_z: float | None = None
    value_z: float | None = None
    pe_ttm: float | None = None
    ev_ebitda: float | None = None
    fcf_yield: float | None = None  # 음수도 보존 — 정당한 부정 시그널

    # 3. Peer context (from ScreenedStock — 사전 조립)
    peer_context: list[PeerComparable] = Field(default_factory=list, max_length=5)

    # 4. Time-series context (Bull/Bear context_builder 조립)
    price_summary: PriceSummary
    fundamentals: FundamentalsTimeseries

    # 5. Lineage (audit/재현 — LLM 프롬프트 미노출)
    run_id: str
    screening_s3_key: str
    data_quality_flags: list[str] = Field(default_factory=list)


# ---------- 출력 (LLM 응답을 검증) ----------


class Argument(BaseModel):
    """Bull/Bear 의 단일 논거.

    claim 은 한 문장 핵심 주장, evidence 는 StockContext 의 어느 수치/사실에서
    도출됐는지 — 시스템 프롬프트가 "데이터에 없는 추정 금지" 를 강제하므로
    evidence 가 빈 문자열로 들어오면 LLM 이 규칙 위반 — Field min_length 로 차단.
    """

    claim: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    confidence: Literal["low", "medium", "high"]


class BullBearOpinion(BaseModel):
    """Bull/Bear 단일 호출의 검증된 출력.

    길이 제약 (docs §2.2):
    - summary: 200자 이내
    - arguments: 3~5개 (단일 호출 패턴의 시그널 농도 보장)
    - key_risks_to_thesis: 1~3개 (자기 입장 반증 강제 — Self-Critique 변형)

    토큰/비용 필드는 schema 가 음수만 차단 — 0 은 허용 (재시도 누적 합산 시
    개별 호출이 0 으로 기록되는 케이스 가능).

    stance 와 본문 일관성(예: bear 에 매수 추천이 섞여 있음) 검증은 schema
    레벨에서 강제 어려움 — 시스템 프롬프트로 1차 방어, 골든 케이스로 회귀 감시.
    """

    symbol: str
    stance: Literal["bull", "bear"]
    as_of_date: date
    summary: str = Field(min_length=1, max_length=200)
    arguments: list[Argument] = Field(min_length=3, max_length=5)
    key_risks_to_thesis: list[str] = Field(min_length=1, max_length=3)

    # 호출 메타 (CLAUDE.md 로깅 규칙 — timestamp/purpose 는 호출 측에서 별도 로깅)
    model: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
