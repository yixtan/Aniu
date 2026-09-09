"""Ports for the operator's watchlist."""

from __future__ import annotations

from typing import Protocol

from backend.business.watchlist.models import WatchlistItem


class WatchlistRepositoryPort(Protocol):
    async def list_items(self) -> list[WatchlistItem]:
        """Every followed company, most recently added first.

        The order is part of the contract rather than a happy accident: the
        page shows how long each has been followed, which only reads well
        when the newest sits at the top.
        """
        ...

    async def get_by_symbol(self, symbol: str) -> WatchlistItem | None: ...

    async def add(self, item: WatchlistItem) -> WatchlistItem: ...

    async def delete(self, item_id: int) -> bool: ...


class StockNameLookupPort(Protocol):
    """Resolves a symbol to the company's name.

    Declared here rather than reaching into stock_api, which this layer may not
    depend on. Returning ``None`` means the name could not be established —
    which is also the only check available that a code names a real company.
    """

    async def name_for(self, symbol: str) -> str | None: ...


__all__ = ["StockNameLookupPort", "WatchlistRepositoryPort"]
