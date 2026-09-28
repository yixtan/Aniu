"""East Money's datacenter report API, read strictly, with "no rows" as normal.

The 龙虎榜, 限售解禁 and 融资融券 reports all come from one gateway:
``datacenter.eastmoney.com/securities/api/data/v1/get`` answered byte for byte
what ``datacenter-web.eastmoney.com/api/data/v1/get`` did on 2026-09-29 (same
body, same version hash), so this module uses the host the F10 calls already
use, on their lane.

The gateway says "nothing matched" as a failure. Asked at 02:31 on 2026-09-29
for 600519's 龙虎榜 from 09-21 to 09-28 — a week it was not listed — it
answered HTTP 200 with::

    {"version":null,"result":null,"success":false,"message":"返回数据为空","code":9201}

Most stocks are not on the 龙虎榜 on most days and most have no 解禁 ahead,
so read the way `eastmoney_research._datacenter_result` reads, every one of
those would reach the model as "source unavailable" and be logged as a failed
call. Here code 9201 is an empty list and nothing else is: any other refusal
is a retryable `UpstreamUnavailable` carrying the gateway's own message,
because a helper that turns every failure into ``[]`` makes "blocked" look
like "not listed".
"""

from __future__ import annotations

from collections.abc import Sequence

from backend.stock_api.public.cancellation import CancellationToken as AbortSignal
from backend.stock_api.public.errors import UpstreamUnavailable
from backend.stock_api.public.providers.base import (
    FixedPublicAdapter,
    build_url,
    public_headers,
)

DATACENTER_ORIGIN = "https://datacenter.eastmoney.com"
DATACENTER_PATH = "/securities/api/data/v1/get"
DATACENTER_REFERER = "https://data.eastmoney.com/"
DATACENTER_LANE = "eastmoney_f10"
DATACENTER_EMPTY_CODES = frozenset({9201, "9201"})
"""``返回数据为空``: the query matched no rows."""


class EastMoneyDatacenterMixin(FixedPublicAdapter):
    """Strict reader for datacenter reports; never sets ``provider`` itself."""

    async def _datacenter_rows(
        self,
        *,
        operation: str,
        endpoint: str,
        report: str,
        filters: Sequence[str],
        sort_columns: Sequence[str],
        sort_types: Sequence[int],
        page_size: int,
        page: int = 1,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> list[dict[str, object]]:
        """Return one page of ``report`` rows; an empty match is ``[]``.

        See `_datacenter_page` for the parameters.
        """

        rows, _ = await self._datacenter_page(
            operation=operation,
            endpoint=endpoint,
            report=report,
            filters=filters,
            sort_columns=sort_columns,
            sort_types=sort_types,
            page_size=page_size,
            page=page,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )
        return rows

    async def _datacenter_page(
        self,
        *,
        operation: str,
        endpoint: str,
        report: str,
        filters: Sequence[str],
        sort_columns: Sequence[str],
        sort_types: Sequence[int],
        page_size: int,
        page: int = 1,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> tuple[list[dict[str, object]], int | None]:
        """One page of ``report`` rows and the gateway's count of all matches.

        The count is None when the gateway gives none, including for an empty
        match.

        ``filters`` are datacenter clauses such as ``SECURITY_CODE="600487"``
        or ``TRADE_DATE>='2026-06-30'``, parenthesised here when they are not
        already; the gateway ANDs them. Codes go in double quotes and dates in
        single quotes. Sort columns and types must pair up: the gateway
        answers a mismatch with code 9501, which would read as an outage.

        One page only. A caller that can exceed ``page_size`` must page itself
        or size the page for the worst day it expects.
        """

        if isinstance(filters, str):
            raise TypeError("datacenter filters are a sequence of clauses")
        if len(sort_columns) != len(sort_types):
            raise ValueError("datacenter sort columns and sort types must pair up")
        params: dict[str, object] = {
            "reportName": report,
            "columns": "ALL",
            "filter": "".join(
                clause if clause.startswith("(") else f"({clause})"
                for clause in filters
            ),
            "pageNumber": page,
            "pageSize": page_size,
            "source": "WEB",
            "client": "WEB",
        }
        if sort_columns:
            params["sortColumns"] = ",".join(sort_columns)
            params["sortTypes"] = ",".join(str(value) for value in sort_types)
        payload = await self._json(
            operation=operation,
            endpoint=endpoint,
            url=build_url(DATACENTER_ORIGIN, DATACENTER_PATH, params),
            parameters={"report": report, "page": page, "page_size": page_size},
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
            headers=public_headers(referer=DATACENTER_REFERER),
            lane=DATACENTER_LANE,
        )
        return datacenter_page(payload, report)


def datacenter_rows(payload: object, report: str) -> list[dict[str, object]]:
    """Read one parsed datacenter answer; see the module docstring."""

    return datacenter_page(payload, report)[0]


def datacenter_page(
    payload: object, report: str
) -> tuple[list[dict[str, object]], int | None]:
    """`datacenter_rows` and ``result.count``, the matches across all pages."""

    if not isinstance(payload, dict):
        raise UpstreamUnavailable(f"东方财富数据中心 {report} 响应无效。")
    if payload.get("success") is not True:
        code = payload.get("code")
        if code in DATACENTER_EMPTY_CODES:
            return [], None
        message = payload.get("message") or payload.get("msg") or "未知错误"
        raise UpstreamUnavailable(
            f"东方财富数据中心 {report} 业务请求失败：{message}（code {code}）。",
            error_category="business_failure",
        )
    result = payload.get("result")
    rows = result.get("data") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise UpstreamUnavailable(f"东方财富数据中心 {report} 响应缺少数据列表。")
    count = result.get("count") if isinstance(result, dict) else None
    return (
        [row for row in rows if isinstance(row, dict)],
        count if isinstance(count, int) and not isinstance(count, bool) else None,
    )


__all__ = [
    "DATACENTER_EMPTY_CODES",
    "DATACENTER_LANE",
    "EastMoneyDatacenterMixin",
    "datacenter_page",
    "datacenter_rows",
]
