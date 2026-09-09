"""Application service for the operator's watchlist."""

from __future__ import annotations

from backend.business.shared.ports import CommitterPort
from backend.business.watchlist.dto import WatchlistItemDTO, to_watchlist_item_dto
from backend.business.watchlist.models import (
    MAX_FOLLOWED,
    WatchlistItem,
    normalize_symbol,
)
from backend.business.watchlist.ports import (
    StockNameLookupPort,
    WatchlistRepositoryPort,
)


class WatchlistAlreadyFollowedError(ValueError):
    """Raised when a symbol is already on the list."""


class StockNameUnavailableError(RuntimeError):
    """Raised when a symbol's name could not be resolved."""


class WatchlistFullError(ValueError):
    """Raised when the list already holds as many companies as it may."""


class WatchlistService:
    """Read and edit which companies the operator follows.

    Every write goes through here rather than through the agent: the list
    records a person's interest, so an agent adding to it would slowly turn it
    into a record of the agent's own.
    """

    def __init__(
        self,
        repository: WatchlistRepositoryPort,
        *,
        names: StockNameLookupPort,
        committer: CommitterPort | None = None,
    ) -> None:
        self._repository = repository
        self._names = names
        self._committer = committer

    async def list_items(self) -> list[WatchlistItemDTO]:
        items = await self._repository.list_items()
        return [to_watchlist_item_dto(item) for item in items]

    async def add(self, symbol: str) -> WatchlistItemDTO:
        """Follow one company, naming it from live data.

        The lookup is required rather than best-effort. It is what makes a
        stored row readable, and it is the only evidence available that the
        code names a real company — saving an unnamed row would hide a typo
        until a run tried to analyse it.
        """

        normalized = normalize_symbol(symbol)
        existing = await self._repository.get_by_symbol(normalized)
        if existing is not None:
            raise WatchlistAlreadyFollowedError(f"{normalized} 已在关注清单中")
        # Checked before the lookup: no reason to call a provider for a company
        # that cannot be stored anyway.
        followed = await self._repository.list_items()
        if len(followed) >= MAX_FOLLOWED:
            raise WatchlistFullError(
                f"关注清单最多 {MAX_FOLLOWED} 只，请先移除不再关注的"
            )
        name = await self._names.name_for(normalized)
        if not name:
            raise StockNameUnavailableError(
                f"查不到 {normalized} 的名称，请确认代码无误后重试"
            )
        stored = await self._repository.add(
            WatchlistItem(id=0, symbol=normalized, name=name)
        )
        await self._commit()
        return to_watchlist_item_dto(stored)

    async def delete(self, item_id: int) -> bool:
        deleted = await self._repository.delete(item_id)
        if deleted:
            await self._commit()
        return deleted

    async def _commit(self) -> None:
        if self._committer is not None:
            await self._committer.commit()


__all__ = [
    "StockNameUnavailableError",
    "WatchlistAlreadyFollowedError",
    "WatchlistFullError",
    "WatchlistService",
]
