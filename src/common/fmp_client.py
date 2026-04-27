"""FMP API 클라이언트.

이 모듈의 범위: 네트워크 I/O만. 비즈니스 로직은 다른 곳에 위치.
모든 메서드는 원본 dict/list 반환 — 타입 파싱은 호출 측에서 수행.

LLM 사용: 없음.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

FMP_BASE_URL = "https://financialmodelingprep.com/stable"
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 3


class FMPError(Exception):
    """FMP API가 에러 또는 예상치 못한 응답을 반환했을 때 발생."""


class FMPClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = FMP_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = requests.Session()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """지수 백오프로 GET 요청. 최종 실패 시 FMPError 발생."""
        url = f"{self._base_url}/{path.lstrip('/')}"
        query = {"apikey": self._api_key, **(params or {})}

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = self._session.get(url, params=query, timeout=self._timeout)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict) and data.get("Error Message"):
                    raise FMPError(f"FMP error: {data['Error Message']}")
                return data
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                wait = 2**attempt
                logger.warning(
                    "FMP 요청 실패 (시도 %d/%d): %s. %d초 후 재시도",
                    attempt + 1,
                    self._max_retries,
                    exc,
                    wait,
                )
                time.sleep(wait)

        raise FMPError(f"FMP 요청이 {self._max_retries}회 시도 후 실패: {last_exc}")

    def get_key_metrics_ttm(self, symbol: str) -> list[dict[str, Any]]:
        """단일 종목의 TTM key metrics 조회.

        스크리닝의 밸류 팩터(P/E TTM, EV/EBITDA TTM, FCF Yield TTM) + 시총
        을 단일 호출로 제공. P/E 는 응답의 `earningsYieldTTM` 의 역수로 도출
        (FMP 가 직접 `peRatioTTM` 을 제공하지 않음 — 2026-04 검증).

        반환은 list (FMP 의 일관된 형식). 보통 length 1, 빈 list 면 데이터 없음.
        Dual-class 종목(BRK.B)은 자동으로 하이픈 표기로 변환.
        """
        fmp_symbol = symbol.replace(".", "-")
        data = self._get("key-metrics-ttm", params={"symbol": fmp_symbol})
        if isinstance(data, list):
            return data
        logger.warning("%s key-metrics-ttm 응답이 예상과 다름: %s", symbol, data)
        return []

    def get_historical_price(self, symbol: str) -> list[dict[str, Any]]:
        """단일 종목의 전체 일별 OHLCV 이력 조회.

        반환 값: date, open, high, low, close, adjClose, volume 등의 키를 가진 dict 리스트.
        Stable API 는 flat array 로 반환. 과거 v3 의 {"symbol", "historical": [...]} 구조도
        안전하게 처리.

        Dual-class 주식(BRK.B, BF.B)의 경우 FMP 는 하이픈 표기(BRK-B, BF-B)를 사용.
        Wikipedia 및 일반 표기(dot form)를 입력으로 받아 자동 변환.
        """
        fmp_symbol = symbol.replace(".", "-")
        data = self._get("historical-price-eod/full", params={"symbol": fmp_symbol})
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "historical" in data:
            return data["historical"]
        logger.warning("%s 에 대한 historical-price 응답이 예상과 다름: %s", symbol, data)
        return []