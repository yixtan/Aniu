"""Presentation shapes for the watchlist."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.business.watchlist.models import WatchlistItem


@dataclass(frozen=True, slots=True)
class WatchlistItemDTO:
    id: int
    symbol: str
    name: str
    created_at: datetime
    updated_at: datetime


def to_watchlist_item_dto(item: WatchlistItem) -> WatchlistItemDTO:
    return WatchlistItemDTO(
        id=item.id,
        symbol=item.symbol,
        name=item.name,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


__all__ = ["WatchlistItemDTO", "to_watchlist_item_dto"]
