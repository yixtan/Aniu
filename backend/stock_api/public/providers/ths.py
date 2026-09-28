"""同花顺 (10jqka) adapter: 涨停归因, 涨停揭秘 and the 人气热榜.

Three hosts and three envelopes. On 2026-09-29 each answered a browser
User-Agent alone — no cookie, no Referer, no hexin-v. That signature guards
iwencai and the industry board summary, and neither is called from here.

同花顺 publishes no API, so a refusal can come back in any shape: a redirect, a
401/403/404, an HTML page where JSON was expected. The transport calls those
final, and the router stops at a final error without trying the next
candidate. For 涨停归因 the next candidate is 涨停揭秘, on another host, and a
refusal from getharden says nothing about it. So every final transport error
from here is raised again as retryable. Nothing else changes: by then the
transport has already stopped sending, and the router is the only reader of
the difference.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from backend.stock_api.public.cancellation import CancellationToken as AbortSignal
from backend.stock_api.public.contracts import (
    HotListRequest,
    LimitPoolRequest,
    LimitReasonsRequest,
    LimitSummaryRequest,
)
from backend.stock_api.public.errors import UpstreamUnavailable
from backend.stock_api.public.providers.base import (
    FixedPublicAdapter,
    build_url,
    public_headers,
)

HARDEN_HOST = "zx.10jqka.com.cn"
HARDEN_PATH = "/event/api/getharden/date/{date}/orderby/date/orderway/desc/charset/GBK/"
"""Every parameter is a path segment. ``orderby``/``orderway`` sort nothing
useful — rows came back in editorial id order on 2026-09-28 — but they are
part of the only URL shape anyone has seen answer."""

LIMIT_UP_POOL_ORIGIN = "https://data.10jqka.com.cn"
LIMIT_UP_POOL_PATH = "/dataapi/limit_up/limit_up_pool"
LIMIT_UP_POOL_FIELDS = (
    "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,"
    "1968584,3475914,9003,9004"
)
"""Opaque 同花顺 column ids, copied as its own page sends them. If it renumbers
them, keys go missing without an error, so the normalizers read every field
as optional."""

LIMIT_UP_POOL_PAGE_SIZE = 200
LIMIT_UP_POOL_MAX_PAGES = 5
"""200 rows held all 33 on 2026-09-28, but a strong day can pass 200. The
counts come from the page-one aggregates either way; the extra pages only
fill in the rows, and five (1000 rows, one a second on the lane) fit the
sentiment budget. The rows arrive latest seal first, so on a day past five
pages the ones left out are the earliest sealed: the 一字 boards and often
the tallest 连板."""

THS_LANE = "ths"

HOT_LIST_ORIGIN = "https://dq.10jqka.com.cn"
HOT_LIST_PATH = "/fuyao/hot_list_data/out/hot_list/v1/stock"


class ThsAdapter(FixedPublicAdapter):
    provider = "ths"

    async def harden(
        self,
        request: LimitReasonsRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        """getharden for ``request.trade_date``: the whole envelope.

        The envelope, not just its rows, because its top-level ``date`` is
        how the normalizer tells this day's list from another day's.

        The probes had only tried ``http://``; https answered too at 03:04 on
        2026-09-29 (200 in 92 ms, the same JSON), so https goes first, once,
        and http is the fallback. The transport moves to http on a failure
        worth repeating (no connection, a timeout). A final answer from
        https — a redirect to http, a 404 from a vhost that stops serving the
        path — gets one http request from here. If https failed the first way
        and http then answered finally, that http request is sent a second
        time: the transport does not say which URL a final error came from.

        ``charset/GBK`` is in the path, so the body is read as GBK. On
        2026-09-28 and 29 it was ASCII with ``\\uXXXX`` escapes, which reads
        the same either way.
        """

        path = HARDEN_PATH.format(date=request.trade_date)
        https_url = f"https://{HARDEN_HOST}{path}"
        http_url = f"http://{HARDEN_HOST}{path}"
        try:
            payload = await self._json(
                operation=request.operation,
                endpoint="ths_getharden",
                url=https_url,
                parameters={"date": request.trade_date},
                timeout_seconds=timeout_seconds,
                cancellation_token=cancellation_token,
                headers=public_headers(),
                encoding="gbk",
                lane=THS_LANE,
                fallback_urls=(http_url,),
            )
        except UpstreamUnavailable as exc:
            if exc.retryable:
                raise
            payload = await self._ths_json(
                request.operation,
                "ths_getharden",
                http_url,
                {"date": request.trade_date},
                timeout_seconds=timeout_seconds,
                cancellation_token=cancellation_token,
                encoding="gbk",
            )
        envelope = _record(payload, "涨停归因")
        # Spelled ``errocode`` by 同花顺; checking ``errorcode`` alone would
        # read every refusal as success.
        code = envelope.get("errocode", envelope.get("errorcode"))
        if code is None:
            raise UpstreamUnavailable(
                "同花顺涨停归因响应缺少 errocode。",
                error_category="invalid_response",
            )
        if code not in (0, "0"):
            message = str(envelope.get("errormsg") or "").strip() or "未说明原因"
            raise UpstreamUnavailable(
                f"同花顺涨停归因返回错误：{message}（{code}）。",
                error_category="business_failure",
            )
        return envelope

    async def limit_up_pool(
        self,
        request: LimitPoolRequest | LimitSummaryRequest | LimitReasonsRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        """涨停揭秘 limit_up_pool for ``request.trade_date``.

        Shared raw shape, read by `normalizers/limit_pools.py` and
        `normalizers/ths.py`: the response's ``data`` object, a dict with
        ``info`` (rows), ``page``, ``limit_up_count``, ``limit_down_count``,
        ``date`` (YYYYMMDD) and ``trade_status``. It is unchanged, with two
        exceptions. When the day runs past one page, ``info`` holds the rows
        of every page fetched. A ``data: null`` comes back as ``{}``; that
        has not been seen, but both normalizers read a dict.

        ``trade_status`` describes the market at the moment of asking
        (「未开盘」 at 01:45), not the requested day.
        """

        day = request.trade_date.replace("-", "")
        data = await self._limit_up_page(
            request, day, 1, timeout_seconds, cancellation_token
        )
        rows = data.get("info")
        if not isinstance(rows, list):
            return data
        pages = min(_page_count(data.get("page")), LIMIT_UP_POOL_MAX_PAGES)
        merged = list(rows)
        for page in range(2, pages + 1):
            more = await self._limit_up_page(
                request, day, page, timeout_seconds, cancellation_token
            )
            extra = more.get("info")
            if not isinstance(extra, list) or not extra:
                break
            merged.extend(extra)
        data["info"] = merged
        return data

    async def hot_list(
        self,
        request: HotListRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        """The 人气热榜 (``type=day``): the envelope's ``data`` object.

        A fixed 100 rows with no date and no timestamp: it is the ranking at
        the moment of asking, so ``limit`` is applied by the normalizer.
        """

        params = {"stock_type": "a", "type": "day", "list_type": "normal"}
        payload = await self._ths_json(
            request.operation,
            "ths_hot_list",
            build_url(HOT_LIST_ORIGIN, HOT_LIST_PATH, params),
            params,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )
        return _data(payload, "人气热榜")

    async def _limit_up_page(
        self,
        request: LimitPoolRequest | LimitSummaryRequest | LimitReasonsRequest,
        day: str,
        page: int,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> dict[str, object]:
        params: dict[str, object] = {
            "page": page,
            "limit": LIMIT_UP_POOL_PAGE_SIZE,
            "field": LIMIT_UP_POOL_FIELDS,
            # 沪深主板 + 创业板 + 科创板. It leaves ST names and 北交所 out.
            "filter": "HS,GEM2STAR",
            # The order 同花顺's own page asks for. Observed on 2026-09-28
            # (probes/ths-limitup-pool-20260928.json): the rows came back by
            # last seal time, latest first — 330324 looks like the last-seal
            # column and order_type 0 descending. Left as the page sends it
            # until another order has been seen to answer.
            "order_field": "330324",
            "order_type": "0",
            "date": day,
        }
        payload = await self._ths_json(
            request.operation,
            "ths_limit_up_pool",
            build_url(LIMIT_UP_POOL_ORIGIN, LIMIT_UP_POOL_PATH, params),
            params,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )
        return _data(payload, "涨停揭秘")

    async def _ths_json(
        self,
        operation: str,
        endpoint: str,
        url: str,
        parameters: Mapping[str, object],
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
        encoding: str = "utf-8",
    ) -> object:
        try:
            return await self._json(
                operation=operation,
                endpoint=endpoint,
                url=url,
                parameters=parameters,
                timeout_seconds=timeout_seconds,
                cancellation_token=cancellation_token,
                headers=public_headers(),
                encoding=encoding,
                # Named, not left to the provider-name default: every 同花顺
                # host rests and paces together.
                lane=THS_LANE,
            )
        except UpstreamUnavailable as exc:
            if exc.retryable:
                raise
            raise UpstreamUnavailable(
                f"同花顺没有给出可用的数据：{exc}",
                error_category=exc.error_category,
            ) from exc


def _record(payload: object, label: str) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise UpstreamUnavailable(
            f"同花顺{label}响应不是对象。", error_category="invalid_response"
        )
    return payload


def _data(payload: object, label: str) -> dict[str, object]:
    """Check the ``status_code`` envelope 涨停揭秘 and the 人气热榜 share."""

    envelope = _record(payload, label)
    code = envelope.get("status_code")
    if code not in (0, "0"):
        message = str(envelope.get("status_msg") or "").strip() or "未说明原因"
        raise UpstreamUnavailable(
            f"同花顺{label}返回错误：{message}（{code}）。",
            error_category="business_failure",
        )
    data = envelope.get("data")
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise UpstreamUnavailable(
            f"同花顺{label}响应中的 data 不是对象。",
            error_category="invalid_response",
        )
    return data


def _page_count(page: object) -> int:
    """Pages in the day, from ``count`` or else ``total``; one when unsure."""

    if not isinstance(page, dict):
        return 1
    count = page.get("count")
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        return count
    total = page.get("total")
    if isinstance(total, int) and not isinstance(total, bool) and total > 0:
        return math.ceil(total / LIMIT_UP_POOL_PAGE_SIZE)
    return 1


__all__ = ["THS_LANE", "ThsAdapter"]
