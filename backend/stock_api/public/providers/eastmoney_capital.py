"""East Money 限售解禁 and 融资融券 per stock, from the datacenter gateway.

Both reports answered on the host the F10 calls already use, on 2026-09-29 at
about 01:46 for 600487, so they share its lane. Each is one request:

- ``RPT_LIFT_STAGE`` returns every batch a stock has, past and scheduled, in
  one page (600487: 11 rows), so there is no second call for "upcoming". The
  split against today happens in the normalizer, which is told the date.
- ``RPTA_WEB_RZRQ_GGMX`` goes back years (600487: 2,236 rows), so the page is
  sized to what was asked for, newest first.

The two reports filter on different columns — ``SECURITY_CODE`` for 解禁,
``SCODE`` for 两融 — and neither takes the ``SECUCODE`` the F10 calls use. A
filter on a column a report does not have is not guaranteed to fail, so the
normalizers also check that every row is the stock that was asked for.

A stock with no 解禁 on record, or one that is not a 两融 target, comes back
as the gateway's 9201, which `_datacenter_rows` turns into ``[]``.
"""

from __future__ import annotations

from backend.stock_api.public.cancellation import CancellationToken as AbortSignal
from backend.stock_api.public.contracts import (
    LiftScheduleRequest,
    MarginDetailRequest,
    symbol_code,
)
from backend.stock_api.public.providers.eastmoney_datacenter import (
    EastMoneyDatacenterMixin,
)

LIFT_REPORT = "RPT_LIFT_STAGE"
MARGIN_REPORT = "RPTA_WEB_RZRQ_GGMX"
LIFT_PAGE_SIZE = 50
"""Far more batches than any stock has; the probe's 600487 had 11."""


class EastMoneyCapitalMixin(EastMoneyDatacenterMixin):
    """解禁 and 两融 reports; mixed into `EastMoneyAdapter`, no ``provider``."""

    async def lift_schedule(
        self,
        request: LiftScheduleRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        """RPT_LIFT_STAGE, every batch, FREE_DATE descending, pageSize 50."""

        return await self._datacenter_rows(
            operation=request.operation,
            endpoint="em_lift_schedule",
            report=LIFT_REPORT,
            filters=(f'SECURITY_CODE="{symbol_code(request.symbol)}"',),
            sort_columns=("FREE_DATE",),
            sort_types=(-1,),
            page_size=LIFT_PAGE_SIZE,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )

    async def margin_detail(
        self,
        request: MarginDetailRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        """RPTA_WEB_RZRQ_GGMX filtered on SCODE, DATE descending,
        pageSize = ``request.limit``."""

        return await self._datacenter_rows(
            operation=request.operation,
            endpoint="em_margin_detail",
            report=MARGIN_REPORT,
            filters=(f'SCODE="{symbol_code(request.symbol)}"',),
            sort_columns=("DATE",),
            sort_types=(-1,),
            page_size=request.limit,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )


__all__ = ["EastMoneyCapitalMixin"]
