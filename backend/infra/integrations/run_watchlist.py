"""What a run sees of the operator's watchlist."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.infra.repositories import WatchlistRepository


@dataclass(slots=True)
class RunWatchlistQuery:
    """Reads the watchlist on its own session.

    A run holds its session for minutes; this opens and closes one so a list
    read at the start of a run does not sit inside that transaction.
    """

    session_factory: async_sessionmaker[AsyncSession]

    async def followed(self) -> tuple[tuple[str, str], ...]:
        async with self.session_factory() as session:
            items = await WatchlistRepository(session).list_items()
        return tuple((item.symbol, item.name) for item in items)


__all__ = ["RunWatchlistQuery"]
