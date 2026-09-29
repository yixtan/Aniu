"""Every data-tool card in 工具管理 names the sources its tool really uses.

The cards live in business/settings/public_stock_interfaces.py, and business
may not import the router, so they are kept by hand. They drifted. On
2026-09-29 two cards said 东方财富 alone:

- 资讯公告, although its 个股新闻 had always gone to 腾讯 and 新浪.
- 热度板块, although #106 had just sent board money flow to 新浪 first.

This test asks the router instead. It calls every action of every tool, with
every combination of the arguments that pick a route, against a service that
only records requests. It then collects the sources the router would try for
each request.
"""

from __future__ import annotations

import itertools
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from backend.business.account import PositionSnapshot
from backend.business.settings.public_stock_interfaces import (
    PUBLIC_STOCK_TOOL_CATALOG,
)
from backend.infra.integrations.kline_agent_tool import QueryKlineTool
from backend.infra.integrations.market_signal_agent_tools import (
    MarketSentimentTool,
    StockSignalsTool,
)
from backend.infra.integrations.public_stock_agent_tools import (
    StockFundamentalsTool,
    StockIntradayTool,
    StockMoneyFlowTool,
    StockNewsTool,
    StockQuoteTool,
    StockRankingTool,
    StockResearchTool,
)
from backend.stock_api import MxMoniClient, MxResearchClient
from backend.stock_api.aggregate.market_snapshot import MarketSnapshotAggregator
from backend.stock_api.aggregate.snapshots import (
    IndustrySnapshotAggregator,
    PortfolioStockSnapshotAggregator,
    StockAnalysisAggregator,
)
from backend.stock_api.public.contracts import PublicStockRequest
from backend.stock_api.public.errors import (
    InvalidStockRequest,
    UnsupportedStockRequest,
)
from backend.stock_api.public.providers.eastmoney import EastMoneyAdapter
from backend.stock_api.public.providers.sina import SinaAdapter
from backend.stock_api.public.providers.tencent import TencentAdapter
from backend.stock_api.public.providers.ths import ThsAdapter
from backend.stock_api.public.router import PublicProviderAdapters, PublicStockRouter
from backend.stock_api.public.service import StockMarketDataService

_PROVIDER_ORDER = ("tencent", "sina", "eastmoney", "ths", "mx")
"""The order every card lists its sources in."""

_TRADING_MORNING = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


class _AnyAdapter:
    """Answers every adapter method; candidates_for only needs the names."""

    def __getattr__(self, name: str) -> Callable[..., Awaitable[object]]:
        async def method(*_: object, **__: object) -> object:
            return {}

        return method


_ROUTER = PublicStockRouter(
    PublicProviderAdapters(
        eastmoney=cast(EastMoneyAdapter, _AnyAdapter()),
        tencent=cast(TencentAdapter, _AnyAdapter()),
        sina=cast(SinaAdapter, _AnyAdapter()),
        ths=cast(ThsAdapter, _AnyAdapter()),
    )
)


class _RecordingService:
    """Records each request and answers with one plain row.

    The row is not empty, so no tool takes it for missing data and retries
    another day.
    """

    def __init__(self) -> None:
        self.requests: list[PublicStockRequest] = []

    async def execute(
        self, request: PublicStockRequest, cancellation_token: object = None
    ) -> dict[str, object]:
        del cancellation_token
        self.requests.append(request)
        return {
            "data": {"items": [{"name": "样本", "code": "600487"}]},
            "meta": {"source": "eastmoney", "warnings": []},
        }


class _RecordingMx:
    """Stands in for both the MX portfolio and the MX research clients."""

    def __init__(self) -> None:
        self.called = False

    async def get_positions(self) -> list[PositionSnapshot]:
        self.called = True
        return [
            PositionSnapshot(
                symbol=symbol,
                stock_name=name,
                quantity=100,
                avg_cost=10.0,
                current_price=10.0,
                market_value=1000.0,
                profit_ratio=0.0,
            )
            for symbol, name in (("600487", "亨通光电"), ("510300", "沪深300ETF"))
        ]

    async def query_market_data(self, query: str) -> object:
        self.called = True
        return {"query": query}

    async def search_news(self, query: str) -> object:
        self.called = True
        return {"query": query}


_BASE_CALLS: dict[str, list[dict[str, object]]] = {
    "stock_quote": [{"symbols": ["600487"]}],
    "query_kline": [{"instrument": "600487"}, {"instrument": "000001.SH"}],
    "stock_intraday": [{"symbol": "600487"}],
    "stock_ranking": [{"action": "stocks"}, {"action": "sectors"}],
    "stock_money_flow": [
        {"action": "stock_history", "symbol": "600487"},
        {"action": "stock_intraday", "symbol": "600487"},
        {"action": "sector"},
        {"action": "connect"},
    ],
    "stock_fundamentals": [
        {"action": action, "symbol": "600487"}
        for action in (
            "financials",
            "shareholders",
            "valuation",
            "industry_comparison",
            "operating_indicators",
        )
    ],
    "stock_research": [
        {"action": "market_reports"},
        {"action": "stock_reports", "symbol": "600487"},
        {"action": "forecast", "symbol": "600487"},
        {"action": "ratings", "symbol": "600487"},
    ],
    "stock_news": [
        {"action": "feed"},
        {"action": "stock_news", "symbol": "600487"},
        {"action": "announcements", "symbol": "600487"},
        {"action": "search", "keyword": "亨通光电"},
    ],
    "stock_signals": [
        {"action": action, "symbol": "600487"}
        for action in ("dragon_tiger", "lift", "margin")
    ],
    "market_sentiment": [
        {"action": "summary"},
        {"action": "pool", "pool": "limit_up"},
        {"action": "reasons"},
        {"action": "hot_list"},
        {"action": "dragon_tiger"},
    ],
}
"""One call per action with its required arguments. The enum arguments that
steer routing are varied on top of these."""


def _public_tool(name: str, service: _RecordingService) -> Any:
    public = cast(StockMarketDataService, service)
    if name == "query_kline":
        return QueryKlineTool(public_service=public)
    if name in {"stock_signals", "market_sentiment"}:
        dated = StockSignalsTool if name == "stock_signals" else MarketSentimentTool
        return dated(public, now=lambda: _TRADING_MORNING)
    return {
        "stock_quote": StockQuoteTool,
        "stock_intraday": StockIntradayTool,
        "stock_ranking": StockRankingTool,
        "stock_money_flow": StockMoneyFlowTool,
        "stock_fundamentals": StockFundamentalsTool,
        "stock_research": StockResearchTool,
        "stock_news": StockNewsTool,
    }[name](public)


async def _accepted(tool: Any, arguments: dict[str, object]) -> bool:
    try:
        await tool.run(**arguments)
    except (InvalidStockRequest, UnsupportedStockRequest, TypeError):
        return False
    return True


async def _explore_public_tool(name: str) -> tuple[list[PublicStockRequest], bool]:
    service = _RecordingService()
    tool = _public_tool(name, service)
    properties = tool.to_tool_definition()["parameters"]["properties"]
    actions = set(properties.get("action", {}).get("enum") or ())
    called = {str(call["action"]) for call in _BASE_CALLS[name] if "action" in call}
    assert called == actions, f"{name}: add a base call for every action"
    steering = {
        key: list(spec["enum"])
        for key, spec in properties.items()
        if key != "action" and spec.get("enum")
    }
    for base in _BASE_CALLS[name]:
        assert await _accepted(tool, base), f"{name} refused {base}"
        # Which steering arguments this action takes at all, then every
        # combination of their values; a combination the tool refuses is
        # simply not a route.
        own = [
            key
            for key in steering
            if await _accepted(tool, {**base, key: steering[key][0]})
        ]
        for values in itertools.product(*(steering[key] for key in own)):
            await _accepted(tool, {**base, **dict(zip(own, values, strict=True))})
    return service.requests, False


async def _explore_aggregate(name: str) -> tuple[list[PublicStockRequest], bool]:
    service = _RecordingService()
    public = cast(StockMarketDataService, service)
    mx = _RecordingMx()
    if name == "market_snapshot":
        await MarketSnapshotAggregator(public_data=public).snapshot()
    elif name == "industry_snapshot":
        await IndustrySnapshotAggregator(public_data=public).snapshot()
    elif name == "stock_analysis":
        await StockAnalysisAggregator(public_data=public).snapshot("600487")
    elif name == "portfolio_stock_snapshot":
        await PortfolioStockSnapshotAggregator(
            public_data=public,
            research=cast(MxResearchClient, mx),
            portfolio=cast(MxMoniClient, mx),
        ).snapshot()
    else:
        raise AssertionError(f"no explorer for aggregate tool {name}")
    return service.requests, mx.called


async def _sources(name: str) -> tuple[str, ...]:
    explore = _explore_public_tool if name in _BASE_CALLS else _explore_aggregate
    requests, used_mx = await explore(name)
    assert requests, f"{name} made no public request"
    providers = {
        candidate.provider
        for request in requests
        for candidate in _ROUTER.candidates_for(request)
    }
    if used_mx:
        providers.add("mx")
    return tuple(provider for provider in _PROVIDER_ORDER if provider in providers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item", PUBLIC_STOCK_TOOL_CATALOG, ids=lambda item: item.tool_name
)
async def test_each_card_names_the_sources_its_tool_can_reach(item: Any) -> None:
    assert await _sources(item.tool_name) == item.providers
