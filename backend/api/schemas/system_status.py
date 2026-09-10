"""Response shapes for the system-status panel."""

from __future__ import annotations

from datetime import date, datetime

from backend.api.schemas.common import ApiModel


class DailyStatusResponse(ApiModel):
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


class TokenDayResponse(ApiModel):
    day: date
    tokens: int
    runs: int
    dream_tokens: int = 0


class DreamStatusResponse(ApiModel):
    target_date: date
    status: str
    completed_at: datetime | None
    failure_reason: str | None
    created: int
    updated: int
    deleted: int
    total_tokens: int = 0


class SystemStatusResponse(ApiModel):
    generated_at: datetime
    days: list[DailyStatusResponse]
    tokens: list[TokenDayResponse]
    dreams: list[DreamStatusResponse]
    memory_live: int
    memory_deleted: int
    memory_with_lineage: int


__all__ = [
    "DailyStatusResponse",
    "DreamStatusResponse",
    "SystemStatusResponse",
    "TokenDayResponse",
]
