"""East Money 涨停/炸板/跌停/昨日涨停 pools, from push2ex on a lane of its own.

push2ex is the same quote-server family as push2 — the same
``{rc, rt, svr, lt, full, data}`` envelope — but not behind the same refusal:
at 01:45 on 2026-09-29 it answered 4 of 4 in 12–340 ms while push2 had been
hanging up on this machine since the 23rd. So it has its own lane,
``eastmoney_push2ex``, and a rest of push2 does not rest it: LaneRest is kept
per lane. One request at a time and 1.5 s apart, as a-stock-data paces East
Money; the lane's gate in `http.py` does the spacing.

Two of these calls sit in front of 同花顺: the 涨停池 and the four-pool
summary. The router moves on only after a retryable failure, and the
transport calls a redirect, a 403 or 404, or an HTML page where JSON was
expected non-retryable — which is how an anti-bot filter answers. So every
push2ex failure leaves here retryable. The transport has already declined to
send such a request twice; this only lets the router try the next source.

A non-zero ``rc`` is a refusal. ``data: null`` is not: the normalizers read
it as an empty pool, which is what the document says push2ex sends for a
day that has none.
"""

from __future__ import annotations

from backend.stock_api.public.cancellation import CancellationToken as AbortSignal
from backend.stock_api.public.contracts import LimitPoolRequest, LimitSummaryRequest
from backend.stock_api.public.errors import UpstreamUnavailable
from backend.stock_api.public.providers.base import (
    FixedPublicAdapter,
    build_url,
    public_headers,
)

PUSH2EX_LANE = "eastmoney_push2ex"
PUSH2EX_ORIGIN = "https://push2ex.eastmoney.com"
PUSH2EX_REFERER = "https://quote.eastmoney.com/"
_UT = "7eea3edcaed734bea9cbfc24409ed989"
"""The public token East Money's own 涨停板 page sends; not a credential."""

_POOL_PATHS = {
    "limit_up": ("/getTopicZTPool", "fbt:asc"),
    "broken": ("/getTopicZBPool", "fbt:asc"),
    "limit_down": ("/getTopicDTPool", "fund:asc"),
    # Asked for with TODAY's date: it lists the previous trading day's 涨停
    # stocks with today's quotes. On 2026-09-28 that was 09-24's, across the
    # 09-25..27 break.
    "previous_limit_up": ("/getYesterdayZTPool", "zs:desc"),
}
SUMMARY_POOLS = ("limit_up", "broken", "limit_down", "previous_limit_up")


class EastMoneyLimitPoolsMixin(FixedPublicAdapter):
    """push2ex pools; mixed into `EastMoneyAdapter`, never sets ``provider``."""

    async def limit_pool(
        self,
        request: LimitPoolRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        """One pool, ``request.pool``, for ``request.trade_date``."""

        return await self._push2ex_pool(
            operation=request.operation,
            pool=request.pool,
            trade_date=request.trade_date,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )

    async def limit_pools_all(
        self,
        request: LimitSummaryRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        """Return ``{"limit_up": …, "broken": …, "limit_down": …,
        "previous_limit_up": …}``, one pool after another.

        The first failure is re-raised and the rest are not asked for. A
        partial set would give a wrong 炸板率, and every pool that hangs up is
        sent twice by the transport, so going on after one had failed would
        spend the lane's four-failure allowance — and the time 同花顺 needs
        after it — on a call that is already lost.
        """

        pools: dict[str, object] = {}
        for pool in SUMMARY_POOLS:
            pools[pool] = await self._push2ex_pool(
                operation=request.operation,
                pool=pool,
                trade_date=request.trade_date,
                timeout_seconds=timeout_seconds,
                cancellation_token=cancellation_token,
            )
        return pools

    async def _push2ex_pool(
        self,
        *,
        operation: str,
        pool: str,
        trade_date: str,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> dict[str, object]:
        path, sort = _POOL_PATHS[pool]
        params: dict[str, object] = {
            "ut": _UT,
            "dpt": "wz.ztzt",
            # Capital P, lower-case i, as the page sends it. 10000 rows is
            # every stock in one page: on 2026-09-28 tc equalled the rows sent.
            "Pageindex": 0,
            "pagesize": 10000,
            "sort": sort,
            "date": trade_date.replace("-", ""),
        }
        try:
            payload = await self._json(
                operation=operation,
                endpoint=f"em_{pool}_pool",
                url=build_url(PUSH2EX_ORIGIN, path, params),
                parameters={"pool": pool, "date": trade_date},
                timeout_seconds=timeout_seconds,
                cancellation_token=cancellation_token,
                headers=public_headers(referer=PUSH2EX_REFERER),
                lane=PUSH2EX_LANE,
            )
        except UpstreamUnavailable as exc:
            if exc.retryable:
                raise
            raise UpstreamUnavailable(
                str(exc), error_category=exc.error_category
            ) from exc
        return push2ex_payload(payload)


def push2ex_payload(payload: object) -> dict[str, object]:
    """Check the envelope the way `_market_json` does; keep ``data`` as sent."""

    if not isinstance(payload, dict):
        raise UpstreamUnavailable("东方财富涨跌停池响应无效。")
    rc = payload.get("rc")
    if rc not in {0, "0"}:
        message = payload.get("msg") or payload.get("message") or "未知错误"
        raise UpstreamUnavailable(
            f"东方财富涨跌停池业务请求失败：{message}（rc {rc}）。",
            error_category="business_failure",
        )
    return payload


__all__ = [
    "PUSH2EX_LANE",
    "SUMMARY_POOLS",
    "EastMoneyLimitPoolsMixin",
    "push2ex_payload",
]
