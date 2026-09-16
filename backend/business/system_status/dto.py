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
    # Order watches, kept out of every column above. `runs_*` and `tokens`
    # mean analysis runs; a watch is a few tool calls, eighty-odd times a
    # day, and it never renders a summary — folded together, the failure
    # column would drown and the HTML ratio would read as broken.
    watches_completed: int = 0
    watches_failed: int = 0
    watch_tokens: int = 0
    # How much of `tokens` + `watch_tokens` the provider served from its own
    # prompt cache. Inside those numbers, not beside them: a tool loop resends
    # the same prefix every turn, and the provider bills a cache hit at a
    # fraction of fresh input. Without this the headline reads as spend when
    # most of it is a discount. Zero before 2026-09-16, when nothing kept the
    # split — which reads the same as "no cache hit" and is all we can say.
    cached_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ChannelTokensDTO:
    """What one provider was asked to think, on one day.

    Attributed per run rather than per stage: the per-stage token split exists
    in only a small minority of stored traces, so a run is counted whole,
    against the provider that did its work.
    """

    channel_id: int | None
    name: str
    tokens: int


@dataclass(frozen=True, slots=True)
class TokenDayDTO:
    day: date
    tokens: int
    runs: int
    # Analyses and watches together, split by who was asked. Dreams are absent:
    # `memory_dreams` keeps no snapshot, so the provider a dream used was never
    # recorded and cannot be recovered — `dream_tokens` stays on its own.
    channels: tuple[ChannelTokensDTO, ...] = ()
    # Kept apart from `tokens` because a dream is not a run: it reads the whole
    # day's reports and the entire memory library in one go, and folding it in
    # silently would make a quiet trading day look busy.
    dream_tokens: int = 0
    # And a watch is not an analysis run either; see DailyStatusDTO.
    watch_tokens: int = 0
    watches: int = 0
    # The cached part of this whole day — analyses, watches and dreams
    # together, because the question it answers is about the bar as drawn.
    cached_tokens: int = 0


@dataclass(frozen=True, slots=True)
class DreamStatusDTO:
    target_date: date
    status: str
    completed_at: datetime | None
    failure_reason: str | None
    created: int
    updated: int
    deleted: int
    total_tokens: int = 0
    cached_tokens: int = 0


@dataclass(frozen=True, slots=True)
class SystemStatusDTO:
    generated_at: datetime
    days: list[DailyStatusDTO]
    tokens: list[TokenDayDTO]
    dreams: list[DreamStatusDTO]
    memory_live: int
    memory_deleted: int
    memory_with_lineage: int


__all__ = [
    "ChannelTokensDTO",
    "DailyStatusDTO",
    "DreamStatusDTO",
    "SystemStatusDTO",
    "TokenDayDTO",
]
