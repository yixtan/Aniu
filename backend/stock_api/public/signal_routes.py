"""Routes for the stock-signal and market-sentiment requests.

Kept out of ``router.py``, which the original catalog had already brought to
845 of its 1000 lines. `signal_candidates` answers None for every other
request, so the router falls through to its own table.

A 同花顺 candidate is left out when the bundle carries no 同花顺 adapter — only
tests written before it was a source build one without — so a route may end
up with no candidates, which the router reports as having no source.

Every candidate that has a follow-up is marked ``fallback_on_empty``, so a
normalizer that finds nothing usable (NoStockData) hands over rather than
ending the call. An ordinary empty answer — no 跌停 today — is a success with
``items: []`` and never reaches the follow-up.

Each of those candidates also leaves `FALLBACK_RESERVE_SECONDS` of the
operation's total for its follow-up. push2ex takes one request at a time, 1.5 s
apart, and the transport sends a request that times out twice, so a few
parallel calls on a host that drops them silently would otherwise spend the
whole total in the queue and never reach 同花顺, which would have answered.
"""

from __future__ import annotations

from backend.stock_api.public.contracts import (
    DragonTigerMarketRequest,
    DragonTigerStockRequest,
    HotListRequest,
    LiftScheduleRequest,
    LimitPoolRequest,
    LimitReasonsRequest,
    LimitSummaryRequest,
    MarginDetailRequest,
    PublicStockRequest,
)
from backend.stock_api.public.normalizers.capital_events import (
    normalize_lift_schedule,
    normalize_margin_detail,
)
from backend.stock_api.public.normalizers.dragon_tiger import (
    normalize_dragon_tiger_market,
    normalize_dragon_tiger_stock,
)
from backend.stock_api.public.normalizers.limit_pools import (
    normalize_limit_pool,
    normalize_limit_summary,
    normalize_ths_limit_pool,
    normalize_ths_limit_summary,
)
from backend.stock_api.public.normalizers.ths import (
    normalize_ths_harden,
    normalize_ths_hot_list,
    normalize_ths_limit_reasons,
)
from backend.stock_api.public.routing import (
    PublicProviderAdapters,
    RouteCandidate,
    route_candidate,
)

FALLBACK_RESERVE_SECONDS = 8.0
"""One 6 s 同花顺 request plus a page or two at its one-a-second pace."""


def signal_candidates(
    adapters: PublicProviderAdapters, request: PublicStockRequest
) -> list[RouteCandidate] | None:
    """Candidates for the eight signal/sentiment requests, else None."""

    eastmoney = adapters.eastmoney
    ths = adapters.ths
    if isinstance(request, DragonTigerStockRequest):
        return [
            route_candidate(
                "eastmoney",
                "em_dragon_tiger_stock",
                eastmoney.dragon_tiger_stock,
                request,
                lambda raw: normalize_dragon_tiger_stock(raw, request),
            )
        ]
    if isinstance(request, DragonTigerMarketRequest):
        return [
            route_candidate(
                "eastmoney",
                "em_dragon_tiger_market",
                eastmoney.dragon_tiger_market,
                request,
                lambda raw: normalize_dragon_tiger_market(raw, request),
            )
        ]
    if isinstance(request, LiftScheduleRequest):
        return [
            route_candidate(
                "eastmoney",
                "em_lift_schedule",
                eastmoney.lift_schedule,
                request,
                lambda raw: normalize_lift_schedule(raw, request),
            )
        ]
    if isinstance(request, MarginDetailRequest):
        return [
            route_candidate(
                "eastmoney",
                "em_margin_detail",
                eastmoney.margin_detail,
                request,
                lambda raw: normalize_margin_detail(raw, request),
            )
        ]
    if isinstance(request, LimitPoolRequest):
        # 同花顺's 涨停揭秘 is a 涨停 pool only; the other three have no stand-in.
        ths_follows = ths is not None and request.pool == "limit_up"
        pool_candidates = [
            route_candidate(
                "eastmoney",
                f"em_{request.pool}_pool",
                eastmoney.limit_pool,
                request,
                lambda raw: normalize_limit_pool(raw, request),
                fallback_on_empty=ths_follows,
                reserve_seconds=FALLBACK_RESERVE_SECONDS if ths_follows else 0.0,
            )
        ]
        if ths is not None and ths_follows:
            pool_candidates.append(
                route_candidate(
                    "ths",
                    "ths_limit_up_pool",
                    ths.limit_up_pool,
                    request,
                    lambda raw: normalize_ths_limit_pool(raw, request),
                )
            )
        return pool_candidates
    if isinstance(request, LimitSummaryRequest):
        summary_candidates = [
            route_candidate(
                "eastmoney",
                "em_limit_pools",
                eastmoney.limit_pools_all,
                request,
                lambda raw: normalize_limit_summary(raw, request),
                fallback_on_empty=ths is not None,
                reserve_seconds=FALLBACK_RESERVE_SECONDS if ths is not None else 0.0,
            )
        ]
        if ths is not None:
            summary_candidates.append(
                route_candidate(
                    "ths",
                    "ths_limit_up_pool",
                    ths.limit_up_pool,
                    request,
                    lambda raw: normalize_ths_limit_summary(raw, request),
                )
            )
        return summary_candidates
    if isinstance(request, LimitReasonsRequest):
        if ths is None:
            return []
        return [
            route_candidate(
                "ths",
                "ths_getharden",
                ths.harden,
                request,
                lambda raw: normalize_ths_harden(raw, request),
                fallback_on_empty=True,
                reserve_seconds=FALLBACK_RESERVE_SECONDS,
            ),
            route_candidate(
                "ths",
                "ths_limit_up_pool",
                ths.limit_up_pool,
                request,
                lambda raw: normalize_ths_limit_reasons(raw, request),
            ),
        ]
    if isinstance(request, HotListRequest):
        if ths is None:
            return []
        return [
            route_candidate(
                "ths",
                "ths_hot_list",
                ths.hot_list,
                request,
                lambda raw: normalize_ths_hot_list(raw, request),
            )
        ]
    return None


__all__ = ["FALLBACK_RESERVE_SECONDS", "signal_candidates"]
