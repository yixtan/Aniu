"""The benchmark index's daily closes, from the public quote service."""

from __future__ import annotations

from datetime import date

from backend.business.shared.stock_api_source import (
    STOCK_API_SOURCE_OVERVIEW_REFRESH,
    stock_api_source,
)
from backend.stock_api.public import IndexKlineRequest, StockMarketDataService

# The most bars one index request may ask for: about fourteen months of
# trading days, past which the oldest closes stop coming back in one call.
_MAX_BARS = 300


class PublicBenchmarkHistory:
    def __init__(self, public_stock_data: StockMarketDataService) -> None:
        self._public_stock_data = public_stock_data

    async def daily_closes(
        self, symbol: str, *, start: date, end: date
    ) -> dict[date, float]:
        request = IndexKlineRequest(
            symbol=symbol,
            period="day",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            limit=_MAX_BARS,
        )
        with stock_api_source(STOCK_API_SOURCE_OVERVIEW_REFRESH):
            result = await self._public_stock_data.execute(request)
        data = result.get("data")
        bars = data.get("bars") if isinstance(data, dict) else None
        closes: dict[date, float] = {}
        for bar in bars if isinstance(bars, list) else []:
            if not isinstance(bar, dict):
                continue
            day, close = bar.get("time"), bar.get("close")
            if not isinstance(day, str) or isinstance(close, bool):
                continue
            if not isinstance(close, (int, float)) or close <= 0:
                continue
            try:
                closes[date.fromisoformat(day[:10])] = float(close)
            except ValueError:
                continue
        return closes
