"""Reads the current order plan for a watch, on its own session."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.order_directives import OrderDirective
from backend.infra.repositories import OrderDirectiveRepository


@dataclass(frozen=True, slots=True)
class RunOrderPlanQuery:
    """Same reason as the watchlist query: a run holds its session for a while,
    and a list read at the start should not sit inside that transaction."""

    session_factory: async_sessionmaker[AsyncSession]

    async def current(self) -> tuple[OrderDirective, ...]:
        async with self.session_factory() as session:
            items = await OrderDirectiveRepository(session).list_current()
        return tuple(items)

    async def previous(self) -> tuple[OrderDirective, ...]:
        async with self.session_factory() as session:
            items = await OrderDirectiveRepository(session).list_previous()
        return tuple(items)


__all__ = ["RunOrderPlanQuery"]
