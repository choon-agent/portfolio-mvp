"""하이브리드 covariance 구성 — 순수 함수 (04 §4.2, 03 부록 B 계약 이행).

대각:   시나리오 variance (가격² 공간) → 수익률² 변환 + VAR_FLOOR
비대각: OHLCV 일간 로그수익률 상관 (shrinkage) × 시나리오 σ
PSD:    고유값 클리핑

수치 라이브러리(numpy/pandas)는 optimizer 컨테이너 전용 의존성 —
기존 zip Lambda 번들(agents/screening)로 유입 금지 (04 §7.2).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from optimizer.schemas import CovarianceParams

__all__ = [
    "return_space_variance",
    "log_returns",
    "shrunk_correlation",
    "hybrid_covariance",
]


def return_space_variance(
    variance_price: float, current_price: float, var_floor: float
) -> float:
    """ExpectedReturn.variance (가격² 공간) → 수익률² + floor.

    floor 는 ALL 형 퇴화(bear=base=bull=current → var=0) 종목이 riskless 로
    오판되는 것 차단 — 03 부록 B 가 지정한 4단계 책임.
    """
    if current_price <= 0:
        raise ValueError(f"current_price 는 양수여야 함: {current_price}")
    return max(variance_price / current_price**2, var_floor)


def log_returns(adj_close: pd.Series, window: int) -> pd.Series:
    """일간 adj_close → 로그수익률, 최근 window 개."""
    r = np.log(adj_close.astype(float) / adj_close.astype(float).shift(1))
    return r.dropna().tail(window)


def shrunk_correlation(returns: pd.DataFrame, shrinkage: float) -> pd.DataFrame:
    """표본 상관 + 단순 shrinkage: ρ' = (1-λ)ρ + λI (04 §4.2 v1).

    returns 는 (일자 × 종목) — 결측 일자는 호출 측이 dropna 로 정렬.
    Ledoit-Wolf 는 §9 이월.
    """
    corr = returns.corr()
    eye = pd.DataFrame(
        np.eye(len(corr)), index=corr.index, columns=corr.columns
    )
    return (1 - shrinkage) * corr + shrinkage * eye


def _clip_psd(matrix: np.ndarray, eps: float = 1e-8) -> np.ndarray:
    """고유값 클리핑으로 PSD 보정 (하이브리드 Σ 는 PSD 미보장)."""
    eigval, eigvec = np.linalg.eigh(matrix)
    return eigvec @ np.diag(np.clip(eigval, eps, None)) @ eigvec.T


def hybrid_covariance(
    variances: dict[str, float],
    correlation: pd.DataFrame,
    params: CovarianceParams,
) -> pd.DataFrame:
    """Σ_ij = ρ'_ij × σ_i × σ_j — 대각은 정확히 시나리오 variance (floor 적용 후).

    variances: 수익률² 공간 (return_space_variance 통과 후). correlation 의
    index/columns 와 심볼 집합 일치 필요.
    """
    syms = list(correlation.index)
    if set(syms) != set(variances):
        raise ValueError(
            f"심볼 불일치: corr={sorted(syms)} vs var={sorted(variances)}"
        )
    sigma = np.sqrt(np.array([variances[s] for s in syms]))
    cov = np.outer(sigma, sigma) * correlation.loc[syms, syms].values
    cov = _clip_psd(cov)
    return pd.DataFrame(cov, index=syms, columns=syms)
