"""Ports for the system-status panel."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from backend.business.system_status.models import (
    DataCallFact,
    DreamFact,
    MemoryActivityFact,
    MemoryInventory,
    RunFact,
    ToolCallFact,
)


class SystemStatusRepositoryPort(Protocol):
    """Plain facts, newest or oldest first as the store prefers.

    Every method is a projection, not a summary: the service decides what a
    day is and what counts, so that arithmetic can be pinned down without a
    database.
    """

    async def runs_since(self, since: datetime) -> list[RunFact]: ...

    async def tool_calls_since(self, since: datetime) -> list[ToolCallFact]: ...

    async def data_calls_since(self, since: datetime) -> list[DataCallFact]: ...

    async def memory_activities_since(
        self, since: datetime
    ) -> list[MemoryActivityFact]: ...

    async def memory_activities_for_tasks(
        self, task_ids: Sequence[int]
    ) -> list[MemoryActivityFact]: ...

    async def recent_dreams(self, limit: int) -> list[DreamFact]:
        """Newest target date first."""
        ...

    async def memory_inventory(self) -> MemoryInventory: ...


__all__ = ["SystemStatusRepositoryPort"]
