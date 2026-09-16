"""Domain models for nightly memory-maintenance dreams."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum

DREAM_TASK_TYPE = 4


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


class DreamStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class MemoryDream:
    task_id: int
    target_date: date
    status: DreamStatus = DreamStatus.PENDING
    result: str | None = None
    failure_reason: str | None = None
    # Billed for this dream, or zero when the endpoint reported nothing.
    total_tokens: int = 0
    # The cached part of that total, inside it rather than beside it. A dream
    # re-sends the whole memory library every turn, so most of what it is
    # billed for is the same prefix again — priced far below fresh input.
    cached_tokens: int = 0
    # Which channel was asked. `None` on every dream from before this was
    # recorded — the provider is not recoverable from anything else stored.
    channel_id: int | None = None
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def start(self) -> None:
        if self.status is not DreamStatus.PENDING:
            raise ValueError("only pending dreams can start")
        self.status = DreamStatus.RUNNING
        self.started_at = utc_now()
        self.failure_reason = None

    def retry(self) -> None:
        if self.status not in {DreamStatus.COMPLETED, DreamStatus.FAILED}:
            raise ValueError("only completed or failed dreams can retry")
        self.status = DreamStatus.PENDING
        self.result = None
        self.failure_reason = None
        self.total_tokens = 0
        self.cached_tokens = 0
        self.channel_id = None
        self.started_at = None
        self.completed_at = None

    def complete(
        self,
        result: str,
        total_tokens: int = 0,
        channel_id: int | None = None,
        cached_tokens: int = 0,
    ) -> None:
        if self.status is not DreamStatus.RUNNING:
            raise ValueError("only running dreams can complete")
        self.status = DreamStatus.COMPLETED
        self.result = result.strip() or None
        self.total_tokens = max(0, total_tokens)
        # Never more than the total it is part of, whatever the provider says.
        self.cached_tokens = min(max(0, cached_tokens), self.total_tokens)
        self.channel_id = channel_id
        self.completed_at = utc_now()

    def fail(self, reason: str) -> None:
        if self.status is not DreamStatus.RUNNING:
            raise ValueError("only running dreams can fail")
        self.status = DreamStatus.FAILED
        self.failure_reason = reason.strip() or "dream execution failed"
        self.completed_at = utc_now()


__all__ = ["DREAM_TASK_TYPE", "DreamStatus", "MemoryDream"]
