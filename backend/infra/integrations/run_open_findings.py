"""The open findings a run must answer, read on their own session.

Same reason as the watchlist and the order plan: a run holds its session for
minutes at a time, and a list read at the start has no business sitting inside
that transaction.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.open_findings import MAX_OPEN_FINDINGS, OpenFinding
from backend.infra.repositories.open_finding_repo import OpenFindingRepository


@dataclass(slots=True)
class RunOpenFindingsQuery:
    session_factory: async_sessionmaker[AsyncSession]

    async def current(self) -> tuple[OpenFinding, ...]:
        async with self.session_factory() as session:
            items = await OpenFindingRepository(session).list_open(
                limit=MAX_OPEN_FINDINGS
            )
        return tuple(items)


__all__ = ["RunOpenFindingsQuery"]
