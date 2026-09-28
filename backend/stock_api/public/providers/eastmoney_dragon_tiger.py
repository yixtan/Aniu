"""East Money 龙虎榜: one stock's listings and seats, and a day's whole list.

Three datacenter reports, all probed on 2026-09-29:

- ``RPT_DAILYBILLBOARD_DETAILSNEW`` has one row per listing: a stock, a date
  and one of the exchange's reasons, keyed by ``TRADE_ID``. The same report
  without a code filter is the whole day's list — 67 rows but 55 stocks on
  2026-09-28, because a stock can be listed for several reasons at once.
- ``RPT_BILLBOARD_DAILYDETAILSBUY`` and ``…SELL`` are the top five seats on
  each side. Filtered by date and code they return the seats of every reason
  listed that day, so the normalizer groups them by ``TRADE_ID``, which is an
  int in the listings and a string here.

Most stocks are not on the list, so the listings come first and the two seat
reports only when there is one: one request for a stock that was not listed,
three for one that was. The three run one after another, not together — the
``eastmoney_f10`` lane does no spacing of its own.

The listings look back `LOOKBACK_DAYS`. 600487 was listed on 2026-08-25 and
not since, though it traded heavily through September; a window keeps "not
listed lately" from turning into seats from a listing years old.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from backend.stock_api.public.budget import time_left
from backend.stock_api.public.cancellation import CancellationToken as AbortSignal
from backend.stock_api.public.contracts import (
    DragonTigerMarketRequest,
    DragonTigerStockRequest,
    symbol_code,
)
from backend.stock_api.public.errors import UpstreamUnavailable
from backend.stock_api.public.normalizers.common import normalize_date, text
from backend.stock_api.public.providers.eastmoney_datacenter import (
    EastMoneyDatacenterMixin,
)

LISTINGS_REPORT = "RPT_DAILYBILLBOARD_DETAILSNEW"
BUY_SEATS_REPORT = "RPT_BILLBOARD_DAILYDETAILSBUY"
SELL_SEATS_REPORT = "RPT_BILLBOARD_DAILYDETAILSSELL"

LOOKBACK_DAYS = 90
"""How far back one stock's listings are read, in calendar days."""

SEAT_PAGE_SIZE = 50
"""Five seats per side per reason; a stock listed for three reasons in one
day returns fifteen rows, so the research's page of 10 would cut seats."""

MARKET_PAGE_SIZE = 500
MARKET_MAX_PAGES = 4
"""A normal day is well under one page (67 rows on 2026-09-28), but the
3-day cumulative reason has no top-five cap, so a day when half the market
moves could run past it."""

SEAT_MARGIN_SECONDS = 0.5
"""How long before the router's deadline the seat requests give up, so the
listings are returned rather than cancelled with them."""
SEAT_MINIMUM_SECONDS = 1.0
"""Less time left than this and the seats are not asked for at all."""


def _shanghai_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


class EastMoneyDragonTigerMixin(EastMoneyDatacenterMixin):
    """龙虎榜 reports; mixed into `EastMoneyAdapter`, never sets ``provider``."""

    async def dragon_tiger_stock(
        self,
        request: DragonTigerStockRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        """Return ``{"since", "records", "seat_date", "buy", "sell",
        "seat_error"}``.

        ``since`` is the first day the listings cover; a ``trade_date`` older
        than the window widens it, so the date asked about is inside it.
        Seats are for ``trade_date`` when given, otherwise for the newest
        listing; a given date missing from a page shorter than ``limit`` was
        not a listing, and its seats are not asked for. A seat request that
        fails leaves the listings standing and says why in ``seat_error``:
        whether and why the stock was listed is the part the model asks for,
        the seats are its detail.
        """

        code = symbol_code(request.symbol)
        since = (_shanghai_today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
        if request.trade_date is not None and request.trade_date < since:
            since = request.trade_date
        records = await self._datacenter_rows(
            operation=request.operation,
            endpoint="em_dragon_tiger_stock",
            report=LISTINGS_REPORT,
            filters=(f'SECURITY_CODE="{code}"', f"TRADE_DATE>='{since}'"),
            sort_columns=("TRADE_DATE",),
            sort_types=(-1,),
            page_size=request.limit,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )
        raw: dict[str, object] = {
            "since": since,
            "records": records,
            "seat_date": None,
            "buy": [],
            "sell": [],
            "seat_error": None,
        }
        seat_date = request.trade_date or (
            normalize_date(records[0].get("TRADE_DATE")) if records else None
        )
        if not records or seat_date is None:
            return raw
        raw["seat_date"] = seat_date
        if request.trade_date is not None and len(records) < request.limit:
            # A short page holds every listing since `since`. If none is on
            # the date asked about, its seat tables can only be empty: on
            # 2026-09-29 600487 for 09-28 sent two requests to learn that.
            listed = {normalize_date(row.get("TRADE_DATE")) for row in records}
            if seat_date not in listed:
                return raw
        filters = (f"TRADE_DATE='{seat_date}'", f'SECURITY_CODE="{code}"')
        # The router cancels the whole call at its deadline, listings and all,
        # and each seat request may be sent twice. So the seat phase stops a
        # little before the deadline, and running out of time is one more way
        # for the seats to be missing.
        left = time_left()
        budget = None if left is None else left - SEAT_MARGIN_SECONDS
        if budget is not None and budget < SEAT_MINIMUM_SECONDS:
            raw["seat_error"] = "查询上榜记录已用完时间，没有再查询席位。"
            return raw
        try:
            async with asyncio.timeout(budget):
                await self._seat_tables(
                    raw, request, filters, timeout_seconds, cancellation_token
                )
        except (UpstreamUnavailable, TimeoutError) as error:
            # One side without the other would read as "nobody sold", so a
            # failure on either drops both.
            raw["buy"], raw["sell"] = [], []
            raw["seat_error"] = (
                str(error)
                if isinstance(error, UpstreamUnavailable)
                else "席位查询超过总超时。"
            )
        return raw

    async def _seat_tables(
        self,
        raw: dict[str, object],
        request: DragonTigerStockRequest,
        filters: tuple[str, str],
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> None:
        raw["buy"] = await self._datacenter_rows(
            operation=request.operation,
            endpoint="em_dragon_tiger_buy_seats",
            report=BUY_SEATS_REPORT,
            filters=filters,
            sort_columns=("BUY",),
            sort_types=(-1,),
            page_size=SEAT_PAGE_SIZE,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )
        raw["sell"] = await self._datacenter_rows(
            operation=request.operation,
            endpoint="em_dragon_tiger_sell_seats",
            report=SELL_SEATS_REPORT,
            filters=filters,
            sort_columns=("SELL",),
            sort_types=(-1,),
            page_size=SEAT_PAGE_SIZE,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )

    async def dragon_tiger_market(
        self,
        request: DragonTigerMarketRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        """Return ``{"rows": …, "expected_count": …}`` for
        ``request.trade_date``; the normalizer counts, sorts and applies
        ``request.limit``.

        Sorted by code, not by net amount as the SKILL does, so pages cut at
        a stable place; a row a later page repeats is dropped by ``TRADE_ID``.

        Paging follows the gateway's own ``count`` of the day's rows, not the
        length of a page: another exchange endpoint was seen serving 2000
        rows to a pageSize of 5000, and a page cut short that way would
        otherwise end the loop and pass a partial list off as the whole day.
        ``expected_count`` lets the normalizer say when the rows fell short.
        """

        rows: list[dict[str, object]] = []
        seen: set[str] = set()
        expected: int | None = None
        day = request.trade_date
        for page in range(1, MARKET_MAX_PAGES + 1):
            page_rows, count = await self._datacenter_page(
                operation=request.operation,
                endpoint="em_dragon_tiger_market",
                report=LISTINGS_REPORT,
                filters=(f"TRADE_DATE>='{day}'", f"TRADE_DATE<='{day}'"),
                sort_columns=("SECURITY_CODE", "TRADE_DATE"),
                sort_types=(1, -1),
                page_size=MARKET_PAGE_SIZE,
                page=page,
                timeout_seconds=timeout_seconds,
                cancellation_token=cancellation_token,
            )
            if page == 1:
                expected = count
            for row in page_rows:
                trade_id = text(row.get("TRADE_ID"))
                if trade_id and trade_id in seen:
                    continue
                if trade_id:
                    seen.add(trade_id)
                rows.append(row)
            if not page_rows:
                break
            if expected is None:
                if len(page_rows) < MARKET_PAGE_SIZE:
                    break
            elif len(rows) >= expected:
                break
        return {"rows": rows, "expected_count": expected}


__all__ = [
    "BUY_SEATS_REPORT",
    "EastMoneyDragonTigerMixin",
    "LISTINGS_REPORT",
    "LOOKBACK_DAYS",
    "SELL_SEATS_REPORT",
]
