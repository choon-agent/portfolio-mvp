"""ScreenedStock → StockContext 평탄화 매퍼.

설계 근거: docs/02-bull-bear.md §2.1, 부록 B (인터페이스 계약)

이 모듈의 역할:
- 스크리닝 단계 출력(ScreenedStock) 의 nested 구조를 Bull/Bear 의 평탄
  StockContext 로 1:1 옮긴다 — 값 가공 없음, PeerComparable 도 그대로 패스
- 스크리닝과 Bull/Bear 사이의 결합도를 매퍼 한 곳에 집중시켜, 양쪽 schema
  변경의 영향 범위를 좁힌다 (docs §2.1 평탄화 결정의 핵심 비용 회수 지점)

순수 함수 — 네트워크/S3/AWS/LLM 호출 없음.

매핑 누락 가드 (부록 B):
- _SCREENED_STOCK_HANDLED / _SCREENED_STOCK_INTENTIONALLY_DROPPED 두 frozenset
  의 합이 ScreenedStock.model_fields 와 정확히 일치해야 한다 (test_*_coverage).
- 스크리닝이 신규 필드를 추가하면 가드 테스트가 깨져 매퍼/제외 목록 갱신을
  강제한다 (silent 불일치 차단).
- FactorScores 도 동일 가드. raw 모멘텀(momentum_12_1m, momentum_6m) 은
  z-score 로 대표되어 LLM 미노출 — 의도적 제외.
"""
from __future__ import annotations

from datetime import date

from screening.schemas import ScreenedStock

from agents.bull_bear.schemas import (
    FundamentalsTimeseries,
    PriceSummary,
    StockContext,
)

# ---------- 매핑 가드 상수 ----------
# ScreenedStock 필드 → 매퍼가 처리하는 것 / 의도적으로 누락하는 것.
# 두 셋의 합이 ScreenedStock.model_fields 와 같아야 한다 (test_bullbear_mappers).

_SCREENED_STOCK_HANDLED: frozenset[str] = frozenset(
    {
        "symbol",
        "company_name",
        "sector",
        "sub_sector",
        "composite_score",
        "factors",  # nested → flat 분해, 세부 가드는 _FACTOR_SCORES_*
        "peer_context",
        "data_quality_flags",
    }
)

_SCREENED_STOCK_INTENTIONALLY_DROPPED: frozenset[str] = frozenset(
    {
        # rank 는 다른 종목과의 상대값. Bull/Bear 는 종목 자체를 평가하므로
        # 컨텍스트에 넣으면 LLM 이 "rank 1 이라 좋다" 식의 동어반복 논거를
        # 만들 위험. composite_score 가 이미 상대값을 담고 있어 충분.
        "rank",
    }
)

_FACTOR_SCORES_FLATTENED: frozenset[str] = frozenset(
    {
        "momentum_z",
        "value_z",
        "pe_ttm",
        "ev_ebitda",
        "fcf_yield",
    }
)

_FACTOR_SCORES_INTENTIONALLY_DROPPED: frozenset[str] = frozenset(
    {
        # raw 모멘텀은 z-score 가 대표 — LLM 에 두 표현을 모두 보내면 토큰만
        # 늘고 동일 시그널의 중복 제시로 편향 위험.
        "momentum_12_1m",
        "momentum_6m",
    }
)


# ---------- 매퍼 ----------


def screened_to_context(
    stock: ScreenedStock,
    *,
    as_of_date: date,
    run_id: str,
    screening_s3_key: str,
    price_summary: PriceSummary,
    fundamentals: FundamentalsTimeseries,
) -> StockContext:
    """ScreenedStock + 외부 주입 필드 → StockContext.

    인자:
        stock: 스크리닝 단계가 산출한 단일 종목 결과.
        as_of_date: ScreeningResult.as_of_date (호출 측이 부모로부터 전달).
        run_id: ScreeningResult.run_id (동일 시점 캐시 일관성 추적용).
        screening_s3_key: 입력 ScreeningResult 가 저장된 S3 키 (audit/재현용).
        price_summary: context_builder 가 OHLCV 캐시에서 조립.
        fundamentals: context_builder 가 FMP statements 캐시에서 조립.

    반환:
        평탄화된 StockContext. PeerComparable 은 동일 타입 재사용이므로
        그대로 패스 (변환 없음). data_quality_flags 는 방어적 복사로 입력
        ScreenedStock 의 list 와 분리 — 이후 호출 측이 추가 플래그를 append
        해도 스크리닝 결과는 변경되지 않는다.

    cross-field 검증/스킵 판단(예: 결측 과다 종목 호출 안 함) 은 이 함수의
    책임이 아니다 — context_builder 가 상위에서 결정.
    """
    return StockContext(
        symbol=stock.symbol,
        company_name=stock.company_name,
        sector=stock.sector,
        sub_sector=stock.sub_sector,
        as_of_date=as_of_date,
        composite_score=stock.composite_score,
        momentum_z=stock.factors.momentum_z,
        value_z=stock.factors.value_z,
        pe_ttm=stock.factors.pe_ttm,
        ev_ebitda=stock.factors.ev_ebitda,
        fcf_yield=stock.factors.fcf_yield,
        peer_context=list(stock.peer_context),
        price_summary=price_summary,
        fundamentals=fundamentals,
        run_id=run_id,
        screening_s3_key=screening_s3_key,
        data_quality_flags=list(stock.data_quality_flags),
    )
