"""Persistence adapter for the operator's watchlist."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.business.watchlist import WatchlistItem
from backend.infra.db.models import WatchlistItemModel


def _as_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(tz=UTC)
    return datetime.fromisoformat(value)


def _to_domain(row: WatchlistItemModel) -> WatchlistItem:
    return WatchlistItem(
        id=row.id,
        symbol=row.symbol,
        name=row.name,
        created_at=_as_datetime(row.created_at),
        updated_at=_as_datetime(row.updated_at),
    )


class WatchlistRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_items(self) -> list[WatchlistItem]:
        # Newest first, with the id as a tiebreaker so two rows written in the
        # same millisecond still come back in a stable order.
        statement = select(WatchlistItemModel).order_by(
            WatchlistItemModel.created_at.desc(),
            WatchlistItemModel.id.desc(),
        )
        rows = (await self._session.scalars(statement)).all()
        return [_to_domain(row) for row in rows]

    async def get_by_symbol(self, symbol: str) -> WatchlistItem | None:
        statement = select(WatchlistItemModel).where(
            WatchlistItemModel.symbol == symbol
        )
        row = (await self._session.scalars(statement)).first()
        return None if row is None else _to_domain(row)

    async def add(self, item: WatchlistItem) -> WatchlistItem:
        row = WatchlistItemModel(
            symbol=item.symbol,
            name=item.name,
            created_at=item.created_at.isoformat(),
            updated_at=item.updated_at.isoformat(),
        )
        self._session.add(row)
        await self._session.flush()
        return _to_domain(row)

    async def delete(self, item_id: int) -> bool:
        result = await self._session.execute(
            delete(WatchlistItemModel).where(WatchlistItemModel.id == item_id)
        )
        await self._session.flush()
        return int(getattr(result, "rowcount", 0)) == 1


__all__ = ["WatchlistRepository"]
