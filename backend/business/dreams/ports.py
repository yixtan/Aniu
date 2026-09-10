"""Ports for nightly memory-maintenance dreams."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from backend.business.dreams.models import MemoryDream


class DreamRepositoryPort(Protocol):
    async def next_task_id(self, reference_date: date) -> int: ...

    async def get_by_id(self, task_id: int) -> MemoryDream | None: ...

    async def delete(self, task_id: int) -> bool: ...

    async def get_by_date(self, target_date: date) -> MemoryDream | None: ...

    async def add(self, dream: MemoryDream) -> MemoryDream: ...

    async def save(self, dream: MemoryDream) -> MemoryDream: ...

    async def list_recent(self, *, limit: int, offset: int) -> list[MemoryDream]: ...

    async def count(self) -> int: ...

    async def list_pending(self, *, limit: int = 256) -> list[MemoryDream]: ...

    async def list_running(self) -> list[MemoryDream]: ...


class RunDayQueryPort(Protocol):
    """Which market days produced something worth reflecting on.

    Declared here rather than imported from the runs feature so this module
    stays independent of it, the same way the reports feature declares its own
    read of a run.
    """

    async def recent_days_with_runs(self, *, limit: int) -> list[date]:
        """Market days holding at least one completed run, newest first."""
        ...


@dataclass(frozen=True, slots=True)
class DreamRunResult:
    """What one dream produced, and what the provider billed for it."""

    content: str
    total_tokens: int = 0


class DreamAgentPort(Protocol):
    async def run(self, dream: MemoryDream) -> DreamRunResult: ...


__all__ = [
    "DreamAgentPort",
    "DreamRepositoryPort",
    "DreamRunResult",
    "RunDayQueryPort",
]
