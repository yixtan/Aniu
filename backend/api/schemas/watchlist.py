"""Request and response shapes for the operator's watchlist."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from backend.api.schemas.common import ApiModel


class WatchlistItemResponse(ApiModel):
    id: int
    symbol: str
    name: str
    created_at: datetime
    updated_at: datetime


class WatchlistResponse(ApiModel):
    items: list[WatchlistItemResponse]


class AddWatchlistItemRequest(ApiModel):
    """Only the code: the name is resolved from live quotes when saving."""

    symbol: str = Field(min_length=6, max_length=16)


__all__ = [
    "AddWatchlistItemRequest",
    "WatchlistItemResponse",
    "WatchlistResponse",
]
