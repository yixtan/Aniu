"""Presentation shapes for the system-status panel."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class DailyStatusDTO:
    day: date
    runs_completed: int
    runs_failed: int
    summaries_html: int
    tokens: int
    trades_completed: int
    trades_failed: int
    memory_writes: int
    memory_write_failures: int
    memory_reads: int
    memory_distinct_queries: int
    data_calls: int
    data_call_failures: int


@dataclass(frozen=True, slots=True)
class TokenDayDTO:
    day: date
    tokens: int
    runs: int


@dataclass(frozen=True, slots=True)
class DreamStatusDTO:
    target_date: date
    status: str
    completed_at: datetime | None
    failure_reason: str | None
    created: int
    updated: int
    deleted: int


@dataclass(frozen=True, slots=True)
class SystemStatusDTO:
    generated_at: datetime
    days: list[DailyStatusDTO]
    tokens: list[TokenDayDTO]
    latest_dream: DreamStatusDTO | None
    memory_live: int
    memory_with_lineage: int


__all__ = ["DailyStatusDTO", "DreamStatusDTO", "SystemStatusDTO", "TokenDayDTO"]
