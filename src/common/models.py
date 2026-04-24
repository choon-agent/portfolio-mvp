"""S&P 500 구성종목 데이터용 Pydantic 모델.

LLM 사용: 없음. 순수 데이터 스키마.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class Constituent(BaseModel):
    """S&P 500 편입 이력 1건.

    현재 구성종목이면 date_removed 는 None.
    """

    symbol: str
    company_name: str | None = None
    sector: str | None = None
    sub_sector: str | None = None
    cik: str | None = None  # SEC 고유번호(Central Index Key) — 티커가 바뀌어도 유지되는 식별자
    date_added: date
    date_removed: date | None = None

    @property
    def is_current(self) -> bool:
        return self.date_removed is None


class ConstituentChangeEvent(BaseModel):
    """업데이트 실행 시 감지된 멤버십 변경 이벤트 로그(append-only)."""

    event_date: date  # 변경이 감지된 날짜 (Lambda 실행일)
    event_type: Literal["added", "removed", "metadata_updated"]
    symbol: str
    company_name: str | None = None
    sector: str | None = None
    previous_date_added: date | None = None  # "removed" 이벤트용
    new_date_added: date | None = None  # "added" 이벤트용


class DiffResult(BaseModel):
    """이전 상태와 새 상태의 비교 결과."""

    added: list[Constituent] = Field(default_factory=list)
    removed: list[Constituent] = Field(default_factory=list)
    metadata_changed: list[Constituent] = Field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.metadata_changed)

    @property
    def added_symbols(self) -> list[str]:
        return [c.symbol for c in self.added]

    @property
    def removed_symbols(self) -> list[str]:
        return [c.symbol for c in self.removed]