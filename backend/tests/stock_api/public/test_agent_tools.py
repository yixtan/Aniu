"""Agent contracts for the normalized public stock-data tools."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import cast

import pytest

from backend.agent.tools import ToolRegistry
from backend.infra.integrations.public_stock_agent_tools import (
    register_public_stock_tools,
)
from backend.infra.integrations.tool_policy import SideEffectLevel
from backend.llm.providers.extract import _claude_tools_spec, _openai_tool_spec
from backend.stock_api.public import (
    IntradayRequest,
    PublicStockRequest,
    QuoteSnapshotRequest,
    StockMarketDataService,
)


@dataclass
class RecordingService:
    requests: list[PublicStockRequest] = field(default_factory=list)

    async def execute(
        self, request: PublicStockRequest, _: object | None = None
    ) -> dict[str, object]:
        self.requests.append(request)
        return {"data": {"operation": request.operation}, "meta": {"source": "test"}}


def _registry() -> tuple[ToolRegistry, RecordingService]:
    service = RecordingService()
    registry = ToolRegistry()
    register_public_stock_tools(
        registry,
        service=cast(StockMarketDataService, service),
    )
    return registry, service


def test_public_stock_tools_have_closed_schemas_without_source_controls() -> None:
    registry, _ = _registry()

    assert set(registry.list_tool_names()) == {
        "stock_quote",
        "stock_intraday",
        "stock_ranking",
        "stock_money_flow",
        "stock_fundamentals",
        "stock_research",
        "stock_news",
    }
    for tool in registry.list_tools():
        definition = tool.to_tool_definition()
        parameters = definition["parameters"]
        branches = parameters.get("oneOf", [parameters])
        for branch in branches:
            assert branch["additionalProperties"] is False
            assert "source" not in branch["properties"]
        assert tool.side_effect_level is SideEffectLevel.READ


def test_only_the_plain_quote_reaches_the_order_watch() -> None:
    """The watch acts on a written plan, so it gets prices and no research.

    Settling "cancel if it trades above 445" needs a price, and an unheld
    symbol has none anywhere in the portfolio. Everything else in this
    registry — intraday, rankings, money flow, fundamentals, research, news —
    is material for forming a view, which is the job the watch does not have.
    """

    registry, _ = _registry()

    reachable = {
        tool.name
        for tool in registry.list_tools()
        if "Watch" in tool.enabled_stages
    }

    assert reachable == {"stock_quote"}
    assert all("Run" in tool.enabled_stages for tool in registry.list_tools())


def test_the_flat_schema_names_which_fields_go_with_which_action() -> None:
    """Branches keep incompatible fields apart; a flat schema has to say so.

    The parameters are flattened because a top-level ``oneOf`` reaches the
    model as an object with no fields at all. What the branches encoded moves
    into the discriminator's description, which the model does see.
    """

    registry, _ = _registry()

    def action_hint(tool_name: str) -> str:
        tool = next(tool for tool in registry.list_tools() if tool.name == tool_name)
        parameters = tool.to_tool_definition()["parameters"]
        assert "oneOf" not in parameters
        return cast(str, parameters["properties"]["action"]["description"])

    fundamentals = action_hint("stock_fundamentals")
    assert "action=valuation 时无其他参数" in fundamentals
    assert "action=shareholders 时可选 page、limit" in fundamentals

    research = action_hint("stock_research")
    assert "action=ratings 时必填 symbol" in research
    assert "action=market_reports 时可选 category、days、top、index" in research

    symbol_pattern = next(
        tool for tool in registry.list_tools() if tool.name == "stock_intraday"
    ).to_tool_definition()["parameters"]["properties"]["symbol"]["pattern"]
    assert re.fullmatch(symbol_pattern, "600519")
    assert re.fullmatch(symbol_pattern, "000001")
    assert re.fullmatch(symbol_pattern, "000001.SZ")
    assert re.fullmatch(symbol_pattern, "000001.SH") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "message"),
    [
        (
            "stock_fundamentals",
            {"action": "valuation", "symbol": "600519", "mode": "latest"},
            "不接受参数：mode",
        ),
        (
            "stock_fundamentals",
            {"action": "financials", "symbol": "600519", "mode": "latest", "page": 2},
            "不接受参数：page",
        ),
        (
            "stock_research",
            {"action": "forecast", "symbol": "600519", "mode": "summary", "limit": 5},
            "不接受参数：limit",
        ),
        (
            "stock_research",
            {"action": "stock_reports", "symbol": "600519", "content": "full"},
            "report_id",
        ),
        (
            "stock_quote",
            {"symbols": [f"60{n:04d}" for n in range(21)], "detail": "full"},
            "最多查询 20 个",
        ),
    ],
)
async def test_the_runtime_refuses_what_the_flat_schema_cannot_separate(
    tool_name: str, arguments: dict[str, object], message: str
) -> None:
    """The flat schema admits these; the tool has to be the one that says no.

    It also has to say why in terms the model can act on — the schema change
    moves this from a shape the model cannot violate to a message it has to
    understand.
    """

    registry, _ = _registry()

    with pytest.raises(Exception) as caught:
        await registry.call(tool_name, **arguments)

    assert message in str(caught.value)


def test_action_branches_and_provider_converters_preserve_the_same_schema() -> None:
    registry, _ = _registry()
    definitions = [
        tool.to_tool_definition()
        for tool in registry.list_tools()
        if tool.name
        in {
            "stock_ranking",
            "stock_money_flow",
            "stock_fundamentals",
            "stock_research",
            "stock_news",
        }
    ]
    for definition in definitions:
        parameters = definition["parameters"]
        # Every action has to be listed where the model can read it, and the
        # object stays closed so an invented field is refused rather than
        # silently dropped.
        assert parameters["properties"]["action"]["enum"]
        assert "action" in parameters["required"]
        assert parameters["additionalProperties"] is False

    quote = next(tool for tool in registry.list_tools() if tool.name == "stock_quote")
    quote_definition = quote.to_tool_definition()
    openai = _openai_tool_spec(quote_definition)
    claude = _claude_tools_spec([quote_definition])[0]
    assert openai["function"]["parameters"] == quote_definition["parameters"]
    assert claude["input_schema"] == quote_definition["parameters"]


@pytest.mark.asyncio
async def test_intraday_remains_an_independent_agent_action() -> None:
    registry, service = _registry()

    intraday = await registry.call(
        "stock_intraday",
        symbol="600519.SH",
        days=1,
    )

    assert intraday == {
        "data": {"operation": "chart.intraday"},
        "meta": {"source": "test"},
    }
    assert isinstance(service.requests[0], IntradayRequest)
    assert [request.operation for request in service.requests] == ["chart.intraday"]


@pytest.mark.asyncio
async def test_agent_tools_complete_inferable_exchange_suffixes() -> None:
    registry, service = _registry()

    await registry.call(
        "stock_quote",
        symbols=["600519", "000001"],
        detail="full",
    )
    await registry.call("stock_intraday", symbol="688981", days=1)

    quote = service.requests[0]
    intraday = service.requests[1]
    assert isinstance(quote, QuoteSnapshotRequest)
    assert quote.symbols == ("600519.SH", "000001.SZ")
    assert isinstance(intraday, IntradayRequest)
    assert intraday.symbol == "688981.SH"


@pytest.mark.asyncio
async def test_action_tools_reject_parameters_for_a_different_action() -> None:
    registry, service = _registry()

    with pytest.raises(Exception, match="stock_ranking.sectors 不接受参数：market"):
        await registry.call(
            "stock_ranking",
            action="sectors",
            market="all_a",
        )

    with pytest.raises(
        Exception, match="stock_fundamentals.financials 不接受参数：page"
    ):
        await registry.call(
            "stock_fundamentals",
            action="financials",
            symbol="600519.SH",
            page=1,
        )

    with pytest.raises(Exception, match="stock_research.forecast 不接受参数：limit"):
        await registry.call(
            "stock_research",
            action="forecast",
            symbol="600519.SH",
            limit=5,
        )

    with pytest.raises(Exception, match="report_id"):
        await registry.call(
            "stock_research",
            action="stock_reports",
            symbol="600519.SH",
            content="full",
        )

    assert service.requests == []
