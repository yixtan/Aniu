"""Resolve a symbol to a company name for the watchlist."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.stock_api.public import QuoteSnapshotRequest, StockMarketDataService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class QuoteStockNameLookup:
    """Names a company from a live quote.

    Returns ``None`` rather than raising for anything that goes wrong upstream:
    the caller turns that into one message the operator can act on, and a
    provider outage should not surface as a stack trace on a page about
    following a company.
    """

    service: StockMarketDataService

    async def name_for(self, symbol: str) -> str | None:
        try:
            result = await self.service.execute(
                QuoteSnapshotRequest(symbols=(symbol,))
            )
        except Exception:
            logger.warning(
                "watchlist stock name lookup failed",
                extra={"symbol": symbol},
                exc_info=True,
            )
            return None
        data = result.get("data") if isinstance(result, dict) else None
        quotes = data.get("quotes") if isinstance(data, dict) else None
        if not isinstance(quotes, list) or not quotes:
            return None
        first = quotes[0]
        name = first.get("name") if isinstance(first, dict) else None
        return name.strip() if isinstance(name, str) and name.strip() else None


__all__ = ["QuoteStockNameLookup"]
