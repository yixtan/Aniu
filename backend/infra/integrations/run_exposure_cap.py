"""The caps already declared, read on their own session.

Same reason as the watchlist and the open findings: a run holds its session
for minutes at a time, and a few rows read at the start have no business
sitting inside that transaction.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.exposure import ExposureCap
from backend.infra.repositories.exposure_cap_repo import ExposureCapRepository


@dataclass(slots=True)
class RecentExposureCapsQuery:
    session_factory: async_sessionmaker[AsyncSession]

    async def recent(self, *, limit: int) -> list[ExposureCap]:
        async with self.session_factory() as session:
            return await ExposureCapRepository(session).recent(limit=limit)


__all__ = ["RecentExposureCapsQuery"]
