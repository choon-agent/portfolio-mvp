"""Wikipedia 기반 S&P 500 구성종목 데이터 소스.

FMP 의 sp500-constituent / historical-sp500-constituent 엔드포인트 대체.
반환 dict 의 필드명은 기존 FMP 응답과 동일하게 유지하여
screening.constituents.build_constituents 를 수정 없이 재사용.

LLM 사용: 없음.
"""
from __future__ import annotations

import logging
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
DEFAULT_TIMEOUT = 30
# Wikipedia 는 default requests UA 를 차단할 수 있음
USER_AGENT = "portfolio-mvp/0.1 (research project; https://github.com/choon-agent/portfolio-mvp)"


class WikipediaSourceError(Exception):
    """Wikipedia 페이지 구조가 예상과 다를 때 발생."""


def _fetch_html() -> str:
    resp = requests.get(
        WIKIPEDIA_URL,
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    return resp.text


def _cell_text(cell: Tag) -> str:
    """테이블 셀의 텍스트 추출 — <sup> 각주 제거 후 공백 정리."""
    for sup in cell.find_all("sup"):
        sup.decompose()
    return cell.get_text(strip=True)


def _get_tables(soup: BeautifulSoup) -> list[Tag]:
    tables = soup.find_all("table", class_="wikitable")
    if len(tables) < 2:
        raise WikipediaSourceError(
            f"wikitable 2개 이상 필요, 실제 {len(tables)}개만 발견"
        )
    return tables


def _parse_current_table(table: Tag) -> list[dict[str, Any]]:
    """현재 구성종목 테이블 파싱.

    컬럼 순서 (2026 기준):
      0:Symbol | 1:Security | 2:GICS Sector | 3:GICS Sub-Industry
      4:HQ Location | 5:Date added (ISO YYYY-MM-DD) | 6:CIK | 7:Founded
    """
    body = table.find("tbody")
    if body is None:
        raise WikipediaSourceError("현재 구성종목 테이블에 tbody 없음")

    result: list[dict[str, Any]] = []
    for row in body.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 7:
            continue
        result.append(
            {
                "symbol": _cell_text(cells[0]),
                "name": _cell_text(cells[1]),
                "sector": _cell_text(cells[2]),
                "subSector": _cell_text(cells[3]),
                "dateFirstAdded": _cell_text(cells[5]),
                "cik": _cell_text(cells[6]),
            }
        )
    return result


def _parse_changes_table(table: Tag) -> list[dict[str, Any]]:
    """변경 이력 테이블 파싱.

    병합 헤더: Date | Added(Ticker, Security) | Removed(Ticker, Security) | Reason.
    데이터 행 컬럼: 0:Date | 1:Added Ticker | 2:Added Security
                     3:Removed Ticker | 4:Removed Security | 5:Reason.
    """
    body = table.find("tbody")
    if body is None:
        raise WikipediaSourceError("변경 이력 테이블에 tbody 없음")

    result: list[dict[str, Any]] = []
    for row in body.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 6:
            continue
        result.append(
            {
                "dateAdded": _cell_text(cells[0]),
                "symbol": _cell_text(cells[1]),
                "addedSecurity": _cell_text(cells[2]),
                "removedTicker": _cell_text(cells[3]),
                "removedSecurity": _cell_text(cells[4]),
                "reason": _cell_text(cells[5]),
            }
        )
    return result


def _parse_all() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    html = _fetch_html()
    soup = BeautifulSoup(html, "lxml")
    tables = _get_tables(soup)
    return _parse_current_table(tables[0]), _parse_changes_table(tables[1])


def fetch_current_sp500() -> list[dict[str, Any]]:
    """현재 S&P 500 구성종목 목록. FMP sp500-constituent 응답과 동일 shape."""
    current, _ = _parse_all()
    logger.info("Wikipedia: 현재 S&P 500 구성종목 %d 개 조회", len(current))
    return current


def fetch_sp500_changes() -> list[dict[str, Any]]:
    """S&P 500 편입/편출 이력. FMP historical-sp500-constituent 응답과 동일 shape."""
    _, changes = _parse_all()
    logger.info("Wikipedia: S&P 500 변경 이력 %d 건 조회", len(changes))
    return changes


def fetch_both() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """한 번의 HTTP 요청으로 둘 다 조회 (Lambda 에서 권장)."""
    current, changes = _parse_all()
    logger.info(
        "Wikipedia: 현재 구성종목 %d 개, 변경 이력 %d 건 조회",
        len(current),
        len(changes),
    )
    return current, changes
