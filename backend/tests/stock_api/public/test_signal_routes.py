"""Where the eight signal and sentiment requests go, and in what order.

East Money first wherever it has the data; 同花顺 as the stand-in for the
涨停 side, and the only source for 涨停归因 and the 人气热榜. The registries a
new operation has to appear in are hand-maintained and silent when missed —
an operation outside them is renamed ``public.request`` in traces, is not
cached, or raises a bare KeyError for its timeout — so they are checked here.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest

from backend.business.shared.stock_api_source import (
    PUBLIC_STOCK_OPERATION_IDS,
    STOCK_API_PROVIDERS,
)
from backend.stock_api.public import router as router_module
from backend.stock_api.public import signal_routes
from backend.stock_api.public.contracts import (
    DragonTigerMarketRequest,
    DragonTigerStockRequest,
    HotListRequest,
    KlineRequest,
    LiftScheduleRequest,
    LimitPoolRequest,
    LimitReasonsRequest,
    LimitSummaryRequest,
    MarginDetailRequest,
    PublicStockRequest,
)
from backend.stock_api.public.errors import InvalidStockRequest, UpstreamUnavailable
from backend.stock_api.public.http import _LANE_LABELS, PublicHttpTransport
from backend.stock_api.public.normalizers.common import NormalizedData
from backend.stock_api.public.providers.eastmoney import EastMoneyAdapter
from backend.stock_api.public.providers.eastmoney_limit_pools import SUMMARY_POOLS
from backend.stock_api.public.providers.sina import SinaAdapter
from backend.stock_api.public.providers.tencent import TencentAdapter
from backend.stock_api.public.providers.ths import ThsAdapter
from backend.stock_api.public.router import PublicProviderAdapters, PublicStockRouter
from backend.stock_api.public.service import _ttl_seconds

REQUESTS: tuple[PublicStockRequest, ...] = (
    DragonTigerStockRequest("600487"),
    DragonTigerMarketRequest("2026-09-28"),
    LiftScheduleRequest("600487", as_of="2026-09-29"),
    MarginDetailRequest("600487"),
    LimitPoolRequest("limit_up", "2026-09-28"),
    LimitSummaryRequest("2026-09-28"),
    LimitReasonsRequest("2026-09-28"),
    HotListRequest(),
)


async def _never(*_: object, **__: object) -> object:
    raise AssertionError("a route test must not call an adapter")


def _eastmoney() -> EastMoneyAdapter:
    return cast(
        EastMoneyAdapter,
        SimpleNamespace(
            dragon_tiger_stock=_never,
            dragon_tiger_market=_never,
            lift_schedule=_never,
            margin_detail=_never,
            limit_pool=_never,
            limit_pools_all=_never,
        ),
    )


def _ths() -> ThsAdapter:
    return cast(
        ThsAdapter,
        SimpleNamespace(harden=_never, limit_up_pool=_never, hot_list=_never),
    )


def _router(*, with_ths: bool = True) -> PublicStockRouter:
    return PublicStockRouter(
        PublicProviderAdapters(
            eastmoney=_eastmoney(),
            tencent=cast(TencentAdapter, SimpleNamespace()),
            sina=cast(SinaAdapter, SimpleNamespace()),
            ths=_ths() if with_ths else None,
        )
    )


def route(
    router: PublicStockRouter, request: PublicStockRequest
) -> list[tuple[str, str, bool]]:
    return [
        (c.provider, c.endpoint, c.fallback_on_empty)
        for c in router.candidates_for(request)
    ]


def test_a_candidate_with_a_follow_up_leaves_it_time() -> None:
    router = _router()

    def reserves(request: PublicStockRequest) -> list[float]:
        return [c.reserve_seconds for c in router.candidates_for(request)]

    reserve = signal_routes.FALLBACK_RESERVE_SECONDS
    assert reserves(LimitPoolRequest("limit_up", "2026-09-28")) == [reserve, 0.0]
    assert reserves(LimitSummaryRequest("2026-09-28")) == [reserve, 0.0]
    assert reserves(LimitReasonsRequest("2026-09-28")) == [reserve, 0.0]
    assert reserves(LimitPoolRequest("broken", "2026-09-28")) == [0.0]
    assert reserves(DragonTigerMarketRequest("2026-09-28")) == [0.0]


def test_each_request_goes_where_the_design_puts_it() -> None:
    router = _router()

    assert route(router, DragonTigerStockRequest("600487")) == [
        ("eastmoney", "em_dragon_tiger_stock", False)
    ]
    assert route(router, DragonTigerMarketRequest("2026-09-28")) == [
        ("eastmoney", "em_dragon_tiger_market", False)
    ]
    assert route(router, LiftScheduleRequest("600487", as_of="2026-09-29")) == [
        ("eastmoney", "em_lift_schedule", False)
    ]
    assert route(router, MarginDetailRequest("600487")) == [
        ("eastmoney", "em_margin_detail", False)
    ]
    assert route(router, LimitPoolRequest("limit_up", "2026-09-28")) == [
        ("eastmoney", "em_limit_up_pool", True),
        ("ths", "ths_limit_up_pool", False),
    ]
    assert route(router, LimitSummaryRequest("2026-09-28")) == [
        ("eastmoney", "em_limit_pools", True),
        ("ths", "ths_limit_up_pool", False),
    ]
    assert route(router, LimitReasonsRequest("2026-09-28")) == [
        ("ths", "ths_getharden", True),
        ("ths", "ths_limit_up_pool", False),
    ]
    assert route(router, HotListRequest()) == [("ths", "ths_hot_list", False)]


@pytest.mark.parametrize("pool", ["broken", "limit_down", "previous_limit_up"])
def test_only_the_limit_up_pool_has_a_tonghuashun_stand_in(pool: str) -> None:
    """涨停揭秘 lists 涨停 stocks only; it cannot stand in for the other pools."""

    request = LimitPoolRequest(pool, "2026-09-28")  # type: ignore[arg-type]

    assert route(_router(), request) == [("eastmoney", f"em_{pool}_pool", False)]


def test_without_a_tonghuashun_adapter_its_candidates_are_left_out() -> None:
    router = _router(with_ths=False)

    assert route(router, LimitPoolRequest("limit_up", "2026-09-28")) == [
        ("eastmoney", "em_limit_up_pool", False)
    ]
    assert route(router, LimitSummaryRequest("2026-09-28")) == [
        ("eastmoney", "em_limit_pools", False)
    ]
    assert route(router, LimitReasonsRequest("2026-09-28")) == []
    assert route(router, HotListRequest()) == []


def test_other_requests_fall_through_to_the_routers_own_table() -> None:
    adapters = PublicProviderAdapters(
        eastmoney=_eastmoney(),
        tencent=cast(TencentAdapter, SimpleNamespace()),
        sina=cast(SinaAdapter, SimpleNamespace()),
    )

    assert signal_routes.signal_candidates(adapters, KlineRequest("600487")) is None


@pytest.mark.parametrize("request_", REQUESTS, ids=lambda r: r.operation)
def test_every_new_operation_is_in_the_hand_kept_registries(
    request_: PublicStockRequest,
) -> None:
    category = request_.operation.split(".", maxsplit=1)[0]

    assert request_.operation in PUBLIC_STOCK_OPERATION_IDS
    assert _ttl_seconds(request_) > 0
    assert category in router_module._TOTAL_TIMEOUTS
    assert category in router_module._UPSTREAM_REQUEST_TIMEOUTS


@pytest.mark.parametrize(
    ("request_", "ttl"),
    [
        (DragonTigerStockRequest("600487"), 600),
        (DragonTigerMarketRequest("2026-09-28"), 600),
        (LiftScheduleRequest("600487", as_of="2026-09-29"), 3600),
        (MarginDetailRequest("600487"), 1800),
        (LimitPoolRequest("limit_up", "2026-09-28"), 60),
        (LimitSummaryRequest("2026-09-28"), 60),
        (HotListRequest(), 60),
        (LimitReasonsRequest("2026-09-28"), 600),
    ],
    ids=lambda value: getattr(value, "operation", str(value)),
)
def test_each_operation_is_kept_as_long_as_its_data_stands_still(
    request_: PublicStockRequest, ttl: float
) -> None:
    assert _ttl_seconds(request_) == ttl


def test_the_sentiment_budget_leaves_tonghuashun_a_turn_after_the_last_pool() -> None:
    """The four pools go one at a time, 1.5 s apart; the last may time out
    and, timing out, is sent twice; 同花顺 then needs one full request."""

    gap = PublicHttpTransport()._gates["eastmoney_push2ex"].minimum_start_interval
    per_request = router_module._UPSTREAM_REQUEST_TIMEOUTS["sentiment"]
    last_pool_starts = (len(SUMMARY_POOLS) - 1) * gap
    needed = last_pool_starts + 2 * per_request + per_request

    assert router_module._TOTAL_TIMEOUTS["sentiment"] >= needed
    assert signal_routes.FALLBACK_RESERVE_SECONDS >= per_request
    # The listings request of a 龙虎榜, sent twice, still leaves the seats time.
    assert (
        router_module._TOTAL_TIMEOUTS["signals"]
        > 2 * (router_module._UPSTREAM_REQUEST_TIMEOUTS["signals"])
    )


def test_tonghuashun_and_push2ex_are_known_everywhere_a_provider_or_lane_is() -> None:
    transport = PublicHttpTransport()

    assert "ths" in STOCK_API_PROVIDERS
    assert router_module._PROVIDER_LABELS["ths"] == "同花顺"
    for lane in ("ths", "eastmoney_push2ex"):
        assert lane in transport._gates
        assert lane in _LANE_LABELS
    assert transport._gates["eastmoney_push2ex"].maximum_concurrency == 1
    assert transport._gates["eastmoney_push2ex"].minimum_start_interval == 1.5
    assert transport._gates["ths"].maximum_concurrency == 1
    assert transport._gates["ths"].minimum_start_interval == 1.0


def test_the_signal_mixins_leave_east_moneys_provider_alone() -> None:
    names = [cls.__name__ for cls in EastMoneyAdapter.__mro__]

    assert EastMoneyAdapter.provider == "eastmoney"
    assert names.index("EastMoneyF10Adapter") < names.index("EastMoneyDatacenterMixin")
    for method in (
        "dragon_tiger_stock",
        "dragon_tiger_market",
        "lift_schedule",
        "margin_detail",
        "limit_pool",
        "limit_pools_all",
        "_datacenter_rows",
        "financials",
    ):
        assert callable(getattr(EastMoneyAdapter, method))


@pytest.mark.asyncio
async def test_a_push2ex_outage_hands_the_summary_to_tonghuashun_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback warning names both sources, so 同花顺 needs a label.

    A provider missing from the router's labels raises a bare KeyError after
    the fallback has already succeeded, turning a good answer into a failed
    call.
    """

    async def push2ex_down(*_: object, **__: object) -> object:
        raise UpstreamUnavailable(
            "公开数据源网络请求失败：服务器没有回应就断开了连接。",
            error_category="network",
        )

    async def ths_pool(*_: object, **__: object) -> object:
        return {"info": []}

    monkeypatch.setattr(
        signal_routes,
        "normalize_ths_limit_summary",
        lambda raw, request: NormalizedData(
            {"trade_date": request.trade_date, "items": []}, degraded=True
        ),
    )
    router = PublicStockRouter(
        PublicProviderAdapters(
            eastmoney=cast(
                EastMoneyAdapter, SimpleNamespace(limit_pools_all=push2ex_down)
            ),
            tencent=cast(TencentAdapter, SimpleNamespace()),
            sina=cast(SinaAdapter, SimpleNamespace()),
            ths=cast(ThsAdapter, SimpleNamespace(limit_up_pool=ths_pool)),
        )
    )

    result = await router.execute(LimitSummaryRequest("2026-09-28"))

    meta = cast(dict[str, object], result["meta"])
    assert (meta["source"], meta["fallback_used"], meta["degraded"]) == (
        "ths",
        True,
        True,
    )
    assert meta["attempted_sources"] == ["eastmoney", "ths"]
    assert "东方财富暂时不可用，已使用同花顺。" in cast(list[str], meta["warnings"])


def test_contracts_refuse_what_the_tool_must_never_send() -> None:
    with pytest.raises(InvalidStockRequest, match="trade_date"):
        LimitSummaryRequest(None)  # type: ignore[arg-type]
    with pytest.raises(InvalidStockRequest, match="pool"):
        LimitPoolRequest("zt", "2026-09-28")  # type: ignore[arg-type]
    with pytest.raises(InvalidStockRequest, match="limit"):
        LimitPoolRequest("limit_up", "2026-09-28", limit=201)
    with pytest.raises(InvalidStockRequest, match="limit"):
        DragonTigerStockRequest("600487", limit=21)
    with pytest.raises(InvalidStockRequest, match="limit"):
        MarginDetailRequest("600487", limit=61)
    with pytest.raises(InvalidStockRequest, match="limit"):
        HotListRequest(limit=101)
    with pytest.raises(InvalidStockRequest, match="as_of"):
        LiftScheduleRequest("600487", as_of="2026/09/29")
    assert DragonTigerStockRequest("600487").symbol == "600487.SH"
    assert LimitReasonsRequest("2026-09-28").limit == 50


_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_parallel_calls_on_a_silent_push2ex_still_reach_tonghuashun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """push2ex takes one request at a time and a silent one holds the gate for
    its whole timeout, twice. Three calls at once used to queue past the
    total, so the 涨停池 failed at 30 s although 同花顺 would have answered.
    Every duration here is the real one × 0.05."""

    scale = 0.05
    monkeypatch.setitem(router_module._TOTAL_TIMEOUTS, "sentiment", 30.0 * scale)
    monkeypatch.setitem(
        router_module._UPSTREAM_REQUEST_TIMEOUTS, "sentiment", 6.0 * scale
    )
    monkeypatch.setattr(signal_routes, "FALLBACK_RESERVE_SECONDS", 8.0 * scale)
    envelope = json.loads(
        (_FIXTURES / "ths_limit_up_pool_20260928.json").read_text(encoding="utf-8")
    )
    envelope["data"] = json.loads(
        (_FIXTURES / "ths_pool_summary_20260928.json").read_text(encoding="utf-8")
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "push2ex.eastmoney.com":
            await asyncio.sleep(100)
        return httpx.Response(200, json=envelope)

    transport = PublicHttpTransport(transport=httpx.MockTransport(handler))
    transport._gates["eastmoney_push2ex"].minimum_start_interval = 1.5 * scale
    transport._gates["ths"].minimum_start_interval = 1.0 * scale
    router = PublicStockRouter(
        PublicProviderAdapters(
            eastmoney=EastMoneyAdapter(transport),
            tencent=cast(TencentAdapter, SimpleNamespace()),
            sina=cast(SinaAdapter, SimpleNamespace()),
            ths=ThsAdapter(transport),
        )
    )

    summary, limit_up, broken = await asyncio.gather(
        router.execute(LimitSummaryRequest("2026-09-28")),
        router.execute(LimitPoolRequest("limit_up", "2026-09-28")),
        router.execute(LimitPoolRequest("broken", "2026-09-28")),
        return_exceptions=True,
    )

    for answer in (summary, limit_up):
        assert isinstance(answer, dict)
        assert cast(dict[str, object], answer["meta"])["source"] == "ths"
    # The 炸板池 has no stand-in; it can only run out of time.
    assert isinstance(broken, UpstreamUnavailable)
