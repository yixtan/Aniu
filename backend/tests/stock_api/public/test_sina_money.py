"""Money flow when push2 will not answer: Sina's endpoints, on Sina's basis.

From 2026-09-23 push2 dropped about half of the calls that only it could
serve: net-inflow rankings, board money flow, the minute flow series. Sina
covers the first two and a day total for the third. The fixtures are real
responses from the evening of 2026-09-28.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest

from backend.stock_api.public.contracts import (
    PublicStockRequest,
    SectorMoneyFlowRequest,
    SectorRankingRequest,
    StockMoneyFlowHistoryRequest,
    StockMoneyFlowIntradayRequest,
    StockRankingRequest,
)
from backend.stock_api.public.errors import NoStockData, UpstreamUnavailable
from backend.stock_api.public.http import PublicHttpTransport
from backend.stock_api.public.normalizers.market import normalize_stock_money_flow
from backend.stock_api.public.normalizers.sina_money import (
    HISTORY_BASIS,
    SECTOR_BASIS,
    STOCK_BASIS,
    TODAY_BASIS,
    normalize_sina_money_history,
    normalize_sina_money_today,
    normalize_sina_sector_money,
    normalize_sina_stock_money_ranking,
)
from backend.stock_api.public.providers.eastmoney import EastMoneyAdapter
from backend.stock_api.public.providers.sina import SinaAdapter
from backend.stock_api.public.providers.tencent import TencentAdapter
from backend.stock_api.public.router import PublicProviderAdapters, PublicStockRouter

_FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> object:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def items_of(data: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], data["items"])


def sina_transport(payload: object) -> tuple[SinaAdapter, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    transport = PublicHttpTransport(transport=httpx.MockTransport(handler))
    transport._gates["sina"].minimum_start_interval = 0
    return SinaAdapter(transport), seen


def test_history_reads_sinas_main_class_not_its_all_order_total() -> None:
    """600487 closed limit-down on 09-28. The old parser showed that day as
    −0.1% and reported the all-order net (−17.5 亿) as 主力 (−12.1 亿)."""

    result = normalize_sina_money_history(
        fixture("sina_money_history.json"),
        StockMoneyFlowHistoryRequest("600487.SH", limit=3),
    )

    items = items_of(result.data)
    assert [item["time"] for item in items] == [
        "2026-09-23",
        "2026-09-24",
        "2026-09-28",
    ]
    latest = items[-1]
    assert latest["main_net_inflow"] == pytest.approx(-1_210_075_931.03)
    assert latest["net_inflow"] == pytest.approx(-1_745_029_883.03)
    assert latest["change_percent"] == pytest.approx(-10.003)
    assert latest["turnover_rate"] == pytest.approx(4.699, abs=1e-3)
    assert result.warnings == (HISTORY_BASIS,)
    assert result.degraded is True


@pytest.mark.asyncio
async def test_history_asks_sina_for_the_latest_days_not_the_biggest() -> None:
    """sort=netamount returned 600487's five biggest inflow days on record,
    from April to August, to a caller asking for the last five."""

    sina, seen = sina_transport(fixture("sina_money_history.json"))

    await sina.stock_money_flow_history(
        StockMoneyFlowHistoryRequest("600487.SH", page=2, limit=5),
        timeout_seconds=2,
        cancellation_token=None,
    )

    params = seen[0].url.params
    assert (params["sort"], params["asc"]) == ("opendate", "0")
    assert (params["page"], params["num"], params["daima"]) == ("2", "5", "sh600487")


def test_eastmoney_history_page_two_holds_the_older_days() -> None:
    """East Money returns the last page × limit days oldest first."""

    four_days = {
        "data": {"klines": [f"2026-09-{day},{day}" for day in (21, 22, 23, 24)]}
    }
    two_days = {"data": {"klines": ["2026-09-23,23", "2026-09-24,24"]}}

    def times(raw: object, page: int) -> list[object]:
        request = StockMoneyFlowHistoryRequest("600487.SH", page=page, limit=2)
        return [
            item["time"]
            for item in items_of(normalize_stock_money_flow(raw, request).data)
        ]

    assert times(two_days, 1) == ["2026-09-23", "2026-09-24"]
    assert times(four_days, 2) == ["2026-09-21", "2026-09-22"]
    with pytest.raises(NoStockData):
        times(four_days, 3)


def test_day_total_stands_in_for_the_minute_series() -> None:
    result = normalize_sina_money_today(
        fixture("sina_money_today.json"), StockMoneyFlowIntradayRequest("600487.SH")
    )

    (item,) = items_of(result.data)
    assert item["time"] == "当日累计"
    assert item["main_net_inflow"] == pytest.approx(-1_210_075_931.03)
    assert item["net_inflow"] == pytest.approx(-1_745_029_883.03)
    assert item["change_percent"] == pytest.approx(-10.003)
    assert result.data["sampled"] is False
    assert result.warnings == (TODAY_BASIS,)


def test_an_empty_day_total_is_no_data_so_the_router_can_move_on() -> None:
    with pytest.raises(NoStockData):
        normalize_sina_money_today(None, StockMoneyFlowIntradayRequest("600487.SH"))
    with pytest.raises(UpstreamUnavailable):
        normalize_sina_money_today(
            {"name": "亨通光电"}, StockMoneyFlowIntradayRequest("600487.SH")
        )


def test_ranking_drops_the_etfs_sina_lists_among_stocks() -> None:
    """Two of the top three rows on 09-28 were government-bond ETFs."""

    result = normalize_sina_stock_money_ranking(
        fixture("sina_money_ranking.json"),
        StockRankingRequest(sort="net_inflow", limit=5),
    )

    (item,) = items_of(result.data)
    assert (item["symbol"], item["name"]) == ("600418.SH", "江淮汽车")
    assert item["net_inflow"] == pytest.approx(1_271_399_024.43)
    assert item["change_percent"] == pytest.approx(9.987, abs=1e-3)
    assert item["turnover_rate"] == pytest.approx(7.962, abs=1e-3)
    assert result.warnings == (STOCK_BASIS,)


@pytest.mark.asyncio
async def test_ranking_fetches_deep_enough_to_fill_the_page_after_etfs_go() -> None:
    sina, seen = sina_transport(fixture("sina_money_ranking.json"))

    for page in (1, 2):
        await sina.stock_money_flow_ranking(
            StockRankingRequest(sort="net_inflow", order="asc", page=page, limit=20),
            timeout_seconds=2,
            cancellation_token=None,
        )

    first, second = (request.url.params for request in seen)
    assert (first["page"], first["num"], first["sort"], first["asc"]) == (
        "1",
        "60",
        "r0_net",
        "1",
    )
    assert (second["page"], second["num"]) == ("1", "100")


def test_ranking_cuts_the_requested_page_after_dropping_etfs() -> None:
    rows = [
        {"symbol": f"sh6000{index:02d}", "name": f"股{index}", "r0_net": 100 - index}
        for index in range(6)
    ]
    rows.insert(1, {"symbol": "sh511010", "name": "国债ETF", "r0_net": 99.5})

    result = normalize_sina_stock_money_ranking(
        rows, StockRankingRequest(sort="net_inflow", page=2, limit=2)
    )

    assert [item["name"] for item in items_of(result.data)] == ["股2", "股3"]


def test_board_money_is_labelled_as_the_all_order_net_on_sinas_boards() -> None:
    result = normalize_sina_sector_money(
        fixture("sina_sector_money.json"),
        SectorMoneyFlowRequest(sector_type="industry", limit=2),
    )

    assert result.data["sector_type"] == "industry"
    first, second = items_of(result.data)
    assert (first["name"], second["name"]) == ("汽车制造", "酿酒行业")
    assert first["id"] == "new_qczz"
    assert first["net_inflow"] == pytest.approx(1_439_083_786.72)
    assert first["change_percent"] == pytest.approx(-0.873, abs=1e-3)
    assert first["price"] is None
    assert (first["leader_symbol"], first["leader_name"]) == ("600418.SH", "江淮汽车")
    assert result.warnings == (SECTOR_BASIS,)


@pytest.mark.asyncio
async def test_board_requests_pick_sinas_board_type_and_sort_field() -> None:
    sina, seen = sina_transport(fixture("sina_sector_money.json"))
    kwargs = {"timeout_seconds": 2, "cancellation_token": None}

    await sina.sector_money_flow(
        SectorMoneyFlowRequest(sector_type="concept"), **kwargs
    )
    await sina.sector_money_flow(
        SectorRankingRequest(sector_type="concept", sort="change_percent", order="asc"),
        **kwargs,
    )
    with pytest.raises(UpstreamUnavailable):
        await sina.sector_money_flow(SectorRankingRequest(sort="volume"), **kwargs)

    flow, change = (request.url.params for request in seen)
    assert (flow["fenlei"], flow["sort"], flow["asc"]) == ("1", "netamount", "0")
    assert (change["fenlei"], change["sort"], change["asc"]) == (
        "1",
        "avg_changeratio",
        "1",
    )


def test_money_flow_routes_put_sina_where_push2_was_the_only_source() -> None:
    async def noop(*_: object, **__: object) -> object:
        return {}

    adapter = SimpleNamespace(
        stock_ranking=noop,
        sector_ranking=noop,
        sector_money_flow=noop,
        stock_money_flow_ranking=noop,
        stock_money_flow_history=noop,
        stock_money_flow_intraday=noop,
        stock_money_flow_today=noop,
    )
    router = PublicStockRouter(
        PublicProviderAdapters(
            eastmoney=cast(EastMoneyAdapter, adapter),
            tencent=cast(TencentAdapter, adapter),
            sina=cast(SinaAdapter, adapter),
        )
    )

    def route(request: PublicStockRequest) -> list[tuple[str, str]]:
        return [(c.provider, c.endpoint) for c in router.candidates_for(request)]

    east_list = ("eastmoney", "em_market_snapshot")
    sina_boards = ("sina", "sina_sector_money_flow")
    assert route(StockRankingRequest(sort="net_inflow")) == [
        ("sina", "sina_stock_money_flow_ranking"),
        east_list,
    ]
    # Sina ranks all A-shares only, and deep pages would over-fetch without end.
    assert route(StockRankingRequest(sort="net_inflow", market="chinext")) == [
        east_list
    ]
    assert route(StockRankingRequest(sort="net_inflow", page=3, limit=40)) == [
        east_list
    ]
    assert route(SectorRankingRequest(sort="net_inflow")) == [sina_boards, east_list]
    assert route(SectorRankingRequest(sector_type="concept")) == [
        sina_boards,
        east_list,
    ]
    assert route(SectorRankingRequest(sector_type="industry")) == [
        ("sina", "sina_industry_overview"),
        east_list,
    ]
    assert route(SectorMoneyFlowRequest()) == [sina_boards, east_list]
    # Only East Money has the minute series, so it still goes first here.
    assert route(StockMoneyFlowIntradayRequest("600487.SH")) == [
        ("eastmoney", "em_money_flow_intraday"),
        ("sina", "sina_stock_money_flow_today"),
    ]


@pytest.mark.asyncio
async def test_minute_flow_falls_back_to_sinas_day_total_when_push2_hangs_up() -> None:
    async def push2_down(*_: object, **__: object) -> object:
        raise UpstreamUnavailable(
            "公开数据源网络请求失败：服务器没有回应就断开了连接。",
            error_category="network",
        )

    async def day_total(*_: object, **__: object) -> object:
        return fixture("sina_money_today.json")

    router = PublicStockRouter(
        PublicProviderAdapters(
            eastmoney=cast(
                EastMoneyAdapter, SimpleNamespace(stock_money_flow_intraday=push2_down)
            ),
            tencent=cast(TencentAdapter, SimpleNamespace()),
            sina=cast(SinaAdapter, SimpleNamespace(stock_money_flow_today=day_total)),
        )
    )

    result = await router.execute(StockMoneyFlowIntradayRequest("600487.SH"))

    meta = cast(dict[str, object], result["meta"])
    assert (meta["source"], meta["fallback_used"], meta["degraded"]) == (
        "sina",
        True,
        True,
    )
    assert TODAY_BASIS in cast(list[str], meta["warnings"])
    data = cast(dict[str, object], result["data"])
    assert items_of(data)[0]["time"] == "当日累计"
