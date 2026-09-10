"""Facts the system-status panel is folded from."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from backend.business.dreams.models import DREAM_TASK_TYPE

# The panel answers "did today go right?", and a week is the context a person
# needs to tell a bad day from a bad week. Tokens are a cost, and a cost reads
# better over a month.
STATUS_WINDOW_DAYS = 7
TOKEN_WINDOW_DAYS = 30
# Enough dreams to see a skipped night and its backfill side by side.
RECENT_DREAMS = 5

# A day is a market day: a run that finishes after midnight UTC still belongs
# to the Shanghai date the operator sees on the runs page.
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")

TRADE_TOOL = "trade"
MEMORY_WRITE_TOOL = "memory_write"


@dataclass(frozen=True, slots=True)
class RunFact:
    started_at: datetime
    status: str
    summary_html: bool
    total_tokens: int


@dataclass(frozen=True, slots=True)
class ToolCallFact:
    """One invocation of a write tool (trade, memory_write, ...) by a run."""

    created_at: datetime
    tool_name: str
    succeeded: bool


@dataclass(frozen=True, slots=True)
class DataCallFact:
    """One data-tool (stock API) call."""

    created_at: datetime
    succeeded: bool


@dataclass(frozen=True, slots=True)
class MemoryActivityFact:
    created_at: datetime
    operation: str
    task_id: int
    query: str


@dataclass(frozen=True, slots=True)
class DreamFact:
    task_id: int
    target_date: date
    status: str
    completed_at: datetime | None
    failure_reason: str | None
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class MemoryInventory:
    live: int
    deleted: int
    # Live memories that record which older ones they were condensed from.
    with_lineage: int


def market_day(moment: datetime) -> date:
    return moment.astimezone(MARKET_TIMEZONE).date()


def is_dream_task(task_id: int) -> bool:
    """Whether a memory activity was the nightly curator's rather than a run's.

    Task ids are YYYYMMDD, one type digit, then a sequence (see
    ``runs.numbering``), so the ninth character says who acted. Rows from
    before task numbering carry 0 and count as a run's.
    """

    text = str(task_id)
    return len(text) > 8 and text[8] == str(DREAM_TASK_TYPE)


__all__ = [
    "MARKET_TIMEZONE",
    "MEMORY_WRITE_TOOL",
    "RECENT_DREAMS",
    "STATUS_WINDOW_DAYS",
    "TOKEN_WINDOW_DAYS",
    "TRADE_TOOL",
    "DataCallFact",
    "DreamFact",
    "MemoryActivityFact",
    "MemoryInventory",
    "RunFact",
    "ToolCallFact",
    "is_dream_task",
    "market_day",
]
