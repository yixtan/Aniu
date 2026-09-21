"""The last declared cap, read on its own session.

Same reason as the watchlist and the open findings: a run holds its session
for minutes at a time, and a single row read at the start has no business
sitting inside that transaction.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.exposure import ExposureCap
from backend.infra.repositories.exposure_cap_repo import ExposureCapRepository


@dataclass(slots=True)
class LatestExposureCapQuery:
    session_factory: async_sessionmaker[AsyncSession]

    async def latest(self) -> ExposureCap | None:
        async with self.session_factory() as session:
            return await ExposureCapRepository(session).latest()


__all__ = ["LatestExposureCapQuery"]
