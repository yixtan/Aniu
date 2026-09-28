"""The two signal tools: which day they ask for, and what they refuse.

The contracts take explicit dates; the tools decide what "today" means. Asked
at 02:30 on Tuesday 2026-09-29, "today's" pools belong to Monday the 28th,
and before the 28th came a three-day holiday, so the day before that is the
24th. A 龙虎榜 asked for at 16:00 is not published yet and comes back empty;
the tool asks once more for the day before and says it did. A pool is never
moved: an empty one is 0 跌停, not "not published".
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from backend.agent.tools import ToolRegistry
from backend.business.stock_api_logs.models import StockApiToolCall
from backend.infra.calendar import MARKET_TIMEZONE
from backend.infra.integrations.agent_runner import _StageToolRegistry
from backend.infra.integrations.agent_runtime import AgentRuntimeFactory
from backend.infra.integrations.market_signal_agent_tools import (
    MarketSentimentTool,
    StockSignalsTool,
    default_trade_date,
)
from backend.infra.integrations.tool_policy import SideEffectLevel
from backend.stock_api.public import (
    DragonTigerMarketRequest,
    DragonTigerStockRequest,
    HotListRequest,
    InvalidStockRequest,
    LiftScheduleRequest,
    LimitPoolRequest,
    LimitSummaryRequest,
    MarginDetailRequest,
    PublicStockRequest,
    StockMarketDataService,
    UnsupportedStockRequest,
    UpstreamUnavailable,
)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=MARKET_TIMEZONE)


def answer(**data: object) -> dict[str, object]:
    return {"data": data, "meta": {"source": "eastmoney", "warnings": []}}


FULL = answer(trade_date="2026-09-29", limit_up_count=33, items=[{"code": "001330"}])
EMPTY = answer(
    trade_date="2026-09-29",
    limit_up_count=0,
    broken_count=0,
    limit_down_count=0,
    previous_limit_up_count=0,
    items=[],
)
EMPTY_POOL = answer(
    trade_date="2026-09-29",
    pool="limit_down",
    count=0,
    note="2026-09-29 的跌停池没有股票。",
    items=[],
)


@dataclass
class FakeService:
    """Answers as the service would, with ``trade_date`` the day asked for."""

    answers: Callable[[PublicStockRequest], dict[str, object]] = lambda _: FULL
    requests: list[PublicStockRequest] = field(default_factory=list)

    async def execute(
        self, request: PublicStockRequest, _: object | None = None
    ) -> dict[str, object]:
        self.requests.append(request)
        result = copy.deepcopy(self.answers(request))
        day = getattr(request, "trade_date", None)
        data = cast(dict[str, object], result["data"])
        if day is not None and "trade_date" in data:
            data["trade_date"] = day
        return result


def tools(
    moment: str = "2026-09-29T10:00:00",
    answers: Callable[[PublicStockRequest], dict[str, object]] = lambda _: FULL,
) -> tuple[ToolRegistry, FakeService]:
    service = FakeService(answers)
    registry = ToolRegistry()
    clock = at(moment)
    for tool in (
        StockSignalsTool(cast(StockMarketDataService, service), now=lambda: clock),
        MarketSentimentTool(cast(StockMarketDataService, service), now=lambda: clock),
    ):
        registry.register(tool)
    return registry, service


def dates(service: FakeService) -> list[str | None]:
    return [getattr(request, "trade_date", None) for request in service.requests]


def warnings(result: object) -> list[str]:
    meta = cast(dict[str, object], cast(dict[str, object], result)["meta"])
    return cast(list[str], meta["warnings"])


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (at("2026-09-29T02:30:00"), "2026-09-28"),
        # 09:15-09:25 is the auction before the match: today's pools are
        # still empty while the 昨日涨停池 is not.
        (at("2026-09-29T09:15:00"), "2026-09-28"),
        (at("2026-09-29T09:24:59"), "2026-09-28"),
        (at("2026-09-29T09:25:00"), "2026-09-29"),
        (at("2026-09-29T16:00:00"), "2026-09-29"),
        # Monday before the auction: the day before is across the holiday.
        (at("2026-09-28T08:00:00"), "2026-09-24"),
        (at("2026-09-26T12:00:00"), "2026-09-24"),
        # 01:30 UTC is 09:30 in Shanghai.
        (datetime(2026, 9, 29, 1, 30, tzinfo=UTC), "2026-09-29"),
    ],
)
def test_the_default_day_is_the_latest_trading_day_whose_data_can_exist(
    moment: datetime, expected: str
) -> None:
    assert default_trade_date(moment).isoformat() == expected


@pytest.mark.asyncio
async def test_a_chosen_day_with_data_is_asked_once() -> None:
    registry, service = tools("2026-09-29T16:00:00")

    result = await registry.call("market_sentiment", action="summary")

    assert dates(service) == ["2026-09-29"]
    assert isinstance(service.requests[0], LimitSummaryRequest)
    assert warnings(result) == []
    assert "trade_date_fallback_from" not in cast(dict[str, object], result)["meta"]


@pytest.mark.asyncio
async def test_an_empty_chosen_day_is_asked_again_for_the_day_before() -> None:
    registry, service = tools(
        "2026-09-28T16:00:00",
        lambda request: (
            EMPTY if getattr(request, "trade_date", None) == "2026-09-28" else FULL
        ),
    )

    result = await registry.call("market_sentiment", action="dragon_tiger")

    assert dates(service) == ["2026-09-28", "2026-09-24"]
    assert all(isinstance(r, DragonTigerMarketRequest) for r in service.requests)
    assert warnings(result) == ["2026-09-28 的数据还没有出来，返回的是 2026-09-24。"]
    meta = cast(dict[str, object], cast(dict[str, object], result)["meta"])
    assert meta["trade_date_fallback_from"] == "2026-09-28"


@pytest.mark.asyncio
async def test_the_day_before_is_asked_only_once_even_if_it_is_empty_too() -> None:
    registry, service = tools("2026-09-29T16:00:00", lambda _: EMPTY)

    result = await registry.call("market_sentiment", action="summary")

    assert dates(service) == ["2026-09-29", "2026-09-28"]
    assert len(warnings(result)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("moment", "day"),
    [
        # During the session, after the match: 0 跌停 on a strong day.
        ("2026-09-29T14:00:00", "2026-09-29"),
        ("2026-09-29T09:31:00", "2026-09-29"),
        # A finished day, chosen before the next day's match.
        ("2026-09-29T08:30:00", "2026-09-28"),
        ("2026-09-29T09:20:00", "2026-09-28"),
    ],
)
async def test_an_empty_pool_is_the_reading_and_is_never_swapped(
    moment: str, day: str
) -> None:
    """Asking the day before would hand over that day's 56-row 跌停池 on a day
    that had none."""

    registry, service = tools(moment, lambda _: EMPTY_POOL)

    result = await registry.call("market_sentiment", action="pool", pool="limit_down")

    assert dates(service) == [day]
    data = cast(dict[str, object], cast(dict[str, object], result)["data"])
    assert (data["trade_date"], data["count"], data["items"]) == (day, 0, [])
    assert not any("返回的是" in warning for warning in warnings(result))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("moment", "arguments", "marked"),
    [
        ("2026-09-29T10:00:00", {"action": "summary"}, True),
        (
            "2026-09-29T10:00:00",
            {"action": "summary", "trade_date": "2026-09-29"},
            True,
        ),
        ("2026-09-29T12:00:00", {"action": "pool", "pool": "broken"}, True),
        ("2026-09-29T14:59:00", {"action": "reasons"}, True),
        ("2026-09-29T15:30:00", {"action": "summary"}, False),
        (
            "2026-09-29T10:00:00",
            {"action": "summary", "trade_date": "2026-09-28"},
            False,
        ),
        # A 龙虎榜 is published after the close; it is never a live reading.
        (
            "2026-09-29T10:00:00",
            {"action": "dragon_tiger", "trade_date": "2026-09-29"},
            False,
        ),
    ],
)
async def test_todays_pools_before_the_close_are_marked_as_a_snapshot(
    moment: str, arguments: dict[str, object], marked: bool
) -> None:
    registry, _ = tools(moment)

    result = await registry.call("market_sentiment", **arguments)

    snapshot = [warning for warning in warnings(result) if "盘中快照" in warning]
    assert bool(snapshot) is marked
    if marked:
        assert f"北京时间 {moment[11:16]}" in snapshot[0]
        assert "收盘前都还会变" in snapshot[0]


@pytest.mark.asyncio
async def test_a_day_the_model_named_is_never_moved() -> None:
    registry, service = tools("2026-09-29T10:00:00", lambda _: EMPTY)

    result = await registry.call(
        "market_sentiment", action="reasons", trade_date="2026-09-26"
    )

    assert dates(service) == ["2026-09-26"]
    assert warnings(result) == []


@pytest.mark.asyncio
async def test_rows_absent_but_a_count_present_is_not_empty() -> None:
    """At 09:26 nothing may have sealed yet, but yesterday's 涨停 trade."""

    registry, service = tools(
        "2026-09-29T09:26:00",
        lambda _: answer(limit_up_count=0, previous_limit_up_count=52, items=[]),
    )

    await registry.call("market_sentiment", action="summary")

    assert dates(service) == ["2026-09-29"]


@pytest.mark.asyncio
async def test_a_future_day_is_refused_before_anything_is_sent() -> None:
    registry, service = tools("2026-09-29T10:00:00")

    with pytest.raises(InvalidStockRequest, match="不能晚于今天（2026-09-29）"):
        await registry.call(
            "market_sentiment", action="summary", trade_date="2026-09-30"
        )
    with pytest.raises(InvalidStockRequest, match="不能晚于今天"):
        await registry.call(
            "stock_signals",
            action="dragon_tiger",
            symbol="600487",
            trade_date="2026-10-08",
        )
    assert service.requests == []


@pytest.mark.asyncio
async def test_today_named_explicitly_is_not_the_future() -> None:
    registry, service = tools("2026-09-29T10:00:00")

    await registry.call("market_sentiment", action="summary", trade_date="2026-09-29")
    await registry.call(
        "market_sentiment", action="dragon_tiger", trade_date="2026-09-29"
    )
    await registry.call(
        "stock_signals",
        action="dragon_tiger",
        symbol="600487",
        trade_date="2026-09-29",
    )

    assert dates(service) == ["2026-09-29", "2026-09-29", "2026-09-29"]
    assert isinstance(service.requests[2], DragonTigerStockRequest)


@pytest.mark.asyncio
async def test_the_hot_list_and_the_stock_signals_take_no_default_day() -> None:
    registry, service = tools("2026-09-29T02:30:00")

    await registry.call("market_sentiment", action="hot_list", limit=20)
    await registry.call("stock_signals", action="dragon_tiger", symbol="600487")
    await registry.call("stock_signals", action="margin", symbol="000001")

    hot_list, dragon_tiger, margin = service.requests
    assert hot_list == HotListRequest(limit=20)
    # None means the latest listing, whenever that was.
    assert dragon_tiger == DragonTigerStockRequest(
        "600487.SH", limit=5, trade_date=None
    )
    assert margin == MarginDetailRequest("000001.SZ", limit=10)


@pytest.mark.asyncio
async def test_lift_splits_history_from_upcoming_at_shanghais_today() -> None:
    service = FakeService()
    tool = StockSignalsTool(
        cast(StockMarketDataService, service),
        # 20:00 UTC on the 28th is already the 29th in Shanghai.
        now=lambda: datetime(2026, 9, 28, 20, 0, tzinfo=UTC),
    )

    await tool.run("lift", symbol="600487")

    assert service.requests == [LiftScheduleRequest("600487.SH", as_of="2026-09-29")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "error", "message"),
    [
        (
            "market_sentiment",
            {"action": "summary", "symbol": "600487"},
            UnsupportedStockRequest,
            "不接受参数：symbol",
        ),
        (
            "market_sentiment",
            {"action": "hot_list", "trade_date": "2026-09-28"},
            UnsupportedStockRequest,
            "不接受参数：trade_date",
        ),
        (
            "market_sentiment",
            {"action": "pool", "trade_date": "2026-09-28"},
            InvalidStockRequest,
            "必须提供参数：pool",
        ),
        (
            "market_sentiment",
            {"action": "pool", "pool": "zt", "trade_date": "2026-09-28"},
            InvalidStockRequest,
            "pool 必须为",
        ),
        (
            "market_sentiment",
            {"action": "pool", "pool": "limit_up", "limit": 201},
            InvalidStockRequest,
            "limit 必须是 1 到 200",
        ),
        (
            "market_sentiment",
            {"action": "summary", "trade_date": "20260928"},
            InvalidStockRequest,
            "yyyy-MM-dd",
        ),
        (
            "market_sentiment",
            {"action": "limit_up"},
            UnsupportedStockRequest,
            "market_sentiment.action 必须为",
        ),
        (
            "stock_signals",
            {"action": "lift", "symbol": "600487", "limit": 5},
            UnsupportedStockRequest,
            "不接受参数：limit",
        ),
        (
            "stock_signals",
            {"action": "margin"},
            InvalidStockRequest,
            "必须提供参数：symbol",
        ),
        (
            "stock_signals",
            {"action": "margin", "symbol": "600487", "limit": 61},
            InvalidStockRequest,
            "limit 必须是 1 到 60",
        ),
        (
            "stock_signals",
            {"action": "dragon_tiger", "symbol": "830799"},
            InvalidStockRequest,
            "仅支持沪深 A 股",
        ),
        (
            "stock_signals",
            {"action": "billboard", "symbol": "600487"},
            UnsupportedStockRequest,
            "stock_signals.action 必须为",
        ),
    ],
)
async def test_the_tools_refuse_what_the_flat_schema_lets_through(
    tool_name: str,
    arguments: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    registry, service = tools()

    with pytest.raises(error, match=message):
        await registry.call(tool_name, **arguments)
    assert service.requests == []


def test_the_schemas_are_closed_and_say_which_fields_go_with_which_action() -> None:
    registry, _ = tools()

    for name, actions, required in (
        ("stock_signals", ["dragon_tiger", "lift", "margin"], ["action", "symbol"]),
        (
            "market_sentiment",
            ["summary", "pool", "reasons", "hot_list", "dragon_tiger"],
            ["action"],
        ),
    ):
        tool = registry.get(name)
        definition = tool.to_tool_definition()
        parameters = definition["parameters"]
        assert "oneOf" not in parameters
        assert parameters["additionalProperties"] is False
        assert parameters["required"] == required
        assert parameters["properties"]["action"]["enum"] == actions
        assert "source" not in parameters["properties"]
        assert "按需调用" in definition["description"]
        assert getattr(tool, "side_effect_level") is SideEffectLevel.READ

    sentiment = registry.get("market_sentiment").to_tool_definition()["parameters"]
    hint = sentiment["properties"]["action"]["description"]
    assert "action=pool 时必填 pool" in hint
    assert "action=hot_list 时可选 limit" in hint
    assert sentiment["properties"]["pool"]["enum"] == [
        "limit_up",
        "broken",
        "limit_down",
        "previous_limit_up",
    ]
    signals = registry.get("stock_signals").to_tool_definition()["parameters"]
    assert "action=lift 时无其他参数" in signals["properties"]["action"]["description"]


@pytest.mark.asyncio
async def test_an_unreachable_source_points_at_what_else_answers() -> None:
    def down(_: PublicStockRequest) -> dict[str, object]:
        raise UpstreamUnavailable(
            "公开数据源网络请求失败：连不上服务器（ConnectError）。",
            error_category="network",
        )

    registry, _ = tools(answers=down)

    with pytest.raises(UpstreamUnavailable, match="select_stocks"):
        await registry.call("market_sentiment", action="hot_list")
    with pytest.raises(UpstreamUnavailable, match="query_market_data"):
        await registry.call("stock_signals", action="margin", symbol="600487")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("category", "message"),
    [
        ("upstream_http", "同花顺没有给出可用的数据：公开数据源返回 HTTP 403。"),
        ("invalid_response", "公开数据源响应不是有效 JSON。"),
        ("business_failure", "东方财富涨跌停池业务请求失败：x（rc 102）。"),
        ("rate_limited", "公开数据源限流（HTTP 429）。"),
        (None, "东方财富涨跌停池响应无效。"),
    ],
)
@pytest.mark.parametrize(
    ("tool_name", "arguments", "instead"),
    [
        ("market_sentiment", {"action": "hot_list"}, "select_stocks"),
        (
            "market_sentiment",
            {"action": "pool", "pool": "limit_down", "trade_date": "2026-09-28"},
            "select_stocks",
        ),
        (
            "market_sentiment",
            {"action": "dragon_tiger", "trade_date": "2026-09-28"},
            "今日龙虎榜",
        ),
        (
            "stock_signals",
            {"action": "margin", "symbol": "600487"},
            "query_market_data",
        ),
    ],
)
async def test_a_refusal_also_points_at_what_else_answers(
    category: str | None,
    message: str,
    tool_name: str,
    arguments: dict[str, object],
    instead: str,
) -> None:
    """push2ex, 同花顺 and the datacenter refuse the way an anti-bot filter does
    — a 403, an HTML page, a refusal code — as often as they hang up, and
    asking again answers the same."""

    def refuse(_: PublicStockRequest) -> dict[str, object]:
        raise UpstreamUnavailable(message, error_category=cast(Any, category))

    registry, _ = tools(answers=refuse)

    with pytest.raises(UpstreamUnavailable) as caught:
        await registry.call(tool_name, **arguments)

    text = str(caught.value)
    assert text.startswith(message)
    assert instead in text
    assert "被拒绝或返回了读不懂的内容" in text
    assert caught.value.error_category == category


@pytest.mark.asyncio
async def test_a_timeout_reads_as_unreachable() -> None:
    def slow(_: PublicStockRequest) -> dict[str, object]:
        raise UpstreamUnavailable("公开数据源请求超时。", error_category="timeout")

    registry, _ = tools(answers=slow)

    with pytest.raises(UpstreamUnavailable, match="连不上.*select_stocks"):
        await registry.call("market_sentiment", action="hot_list")


@pytest.mark.asyncio
async def test_an_oversized_result_is_not_pointed_elsewhere() -> None:
    """The size limit is about the request, not the source."""

    registry, _ = tools(
        "2026-09-29T16:00:00",
        lambda _: answer(trade_date="2026-09-29", count=1, items=[], note="x" * 70_000),
    )

    with pytest.raises(UpstreamUnavailable) as caught:
        await registry.call("market_sentiment", action="reasons")

    assert "select_stocks" not in str(caught.value)


@pytest.mark.asyncio
async def test_both_tools_reach_the_run_and_nothing_else() -> None:
    registry = await AgentRuntimeFactory(
        public_stock_data=cast(StockMarketDataService, object())
    ).build_tool_registry()

    def names(stage: str) -> set[str]:
        stage_registry = _StageToolRegistry(
            registry,
            stage,
            run_id=20260929001,
            invocation_session_factory=None,
        )
        return {str(getattr(tool, "name", "")) for tool in stage_registry.list_tools()}

    for name in ("stock_signals", "market_sentiment"):
        assert registry.get(name).enabled_stages == ("Run",)  # type: ignore[attr-defined]
        assert name in names("Run")
        assert name not in names("Watch")
        assert name not in names("Summary")


@pytest.mark.asyncio
async def test_a_pool_request_carries_the_pool_and_limit_through() -> None:
    registry, service = tools("2026-09-29T15:30:00")

    await registry.call("market_sentiment", action="pool", pool="limit_down", limit=80)

    assert service.requests == [LimitPoolRequest("limit_down", "2026-09-29", limit=80)]


# 龙虎榜 before publication ----------------------------------------------------

LISTED_0825 = {
    "date": "2026-08-25",
    "reason": "有价格涨跌幅限制的日价格振幅达到15%的前五只证券",
    "net_amount": 1849593215.8,
}


def stock_listings(*items: dict[str, object], note: str) -> dict[str, object]:
    return answer(
        symbol="600487.SH",
        since="2026-07-01",
        items=list(items),
        seats=None,
        note=note,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("listings", "tail"),
    [
        ((LISTED_0825,), "最近一次上榜见 items"),
        ((), "2026-07-01 以来没有上过龙虎榜"),
    ],
)
async def test_todays_listing_before_publication_is_unknown_not_absent(
    listings: tuple[dict[str, object], ...], tail: str
) -> None:
    """A 14:50 run asking whether today's limit-down put 600487 on the list
    must not read 「没有上龙虎榜」: the list does not exist yet."""

    flat = "2026-09-29 这只股票没有上龙虎榜；最近的上榜见 items。"
    registry, _ = tools(
        "2026-09-29T14:50:00", lambda _: stock_listings(*listings, note=flat)
    )

    result = await registry.call(
        "stock_signals", action="dragon_tiger", symbol="600487", trade_date="2026-09-29"
    )

    data = cast(dict[str, object], cast(dict[str, object], result)["data"])
    note = cast(str, data["note"])
    assert "没有上龙虎榜" not in note
    assert "17:00–18:00" in note
    assert "现在查不到不代表没上榜" in note
    assert tail in note
    assert any("还判断不了" in warning for warning in warnings(result))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("moment", "trade_date"),
    [
        # Published by then: a miss is a real "not listed".
        ("2026-09-29T19:00:00", "2026-09-29"),
        # Yesterday's list came out yesterday evening.
        ("2026-09-29T10:00:00", "2026-09-28"),
    ],
)
async def test_a_published_day_keeps_its_flat_not_listed(
    moment: str, trade_date: str
) -> None:
    flat = f"{trade_date} 这只股票没有上龙虎榜；最近的上榜见 items。"
    registry, _ = tools(moment, lambda _: stock_listings(LISTED_0825, note=flat))

    result = await registry.call(
        "stock_signals", action="dragon_tiger", symbol="600487", trade_date=trade_date
    )

    data = cast(dict[str, object], cast(dict[str, object], result)["data"])
    assert data["note"] == flat
    assert warnings(result) == []


@pytest.mark.asyncio
async def test_a_listing_already_out_today_is_left_as_it_is() -> None:
    today = {**LISTED_0825, "date": "2026-09-29"}
    registry, _ = tools("2026-09-29T17:40:00", lambda _: stock_listings(today, note=""))

    result = await registry.call(
        "stock_signals", action="dragon_tiger", symbol="600487", trade_date="2026-09-29"
    )

    assert (
        cast(dict[str, object], cast(dict[str, object], result)["data"])["note"] == ""
    )


# The call log ----------------------------------------------------------------


async def logged_parameters(
    moment: str,
    answers: Callable[[PublicStockRequest], dict[str, object]],
    **arguments: object,
) -> dict[str, object]:
    source, _ = tools(moment, answers)
    persisted: list[StockApiToolCall] = []

    async def record(call: StockApiToolCall) -> None:
        persisted.append(call)

    registry = _StageToolRegistry(
        source,
        "Run",
        run_id=20260929001,
        invocation_session_factory=None,
        stock_api_tool_call_logger=record,
    )
    await registry.call_idempotently(
        "market_sentiment", tool_call_id="sentiment-1", abort_signal=None, **arguments
    )
    assert len(persisted) == 1
    return persisted[0].parameters


@pytest.mark.asyncio
async def test_the_log_names_the_day_the_tool_read_and_the_one_it_fell_back_from() -> (
    None
):
    parameters = await logged_parameters(
        "2026-09-28T16:00:00",
        lambda request: (
            EMPTY if getattr(request, "trade_date", None) == "2026-09-28" else FULL
        ),
        action="dragon_tiger",
    )

    assert parameters == {
        "action": "dragon_tiger",
        "trade_date": "2026-09-24",
        "trade_date_fallback_from": "2026-09-28",
    }


@pytest.mark.asyncio
async def test_the_log_names_a_chosen_day_and_leaves_a_named_one_alone() -> None:
    chosen = await logged_parameters(
        "2026-09-29T08:30:00", lambda _: EMPTY_POOL, action="pool", pool="broken"
    )
    named = await logged_parameters(
        "2026-09-29T16:00:00", lambda _: FULL, action="reasons", trade_date="2026-09-26"
    )
    hot = await logged_parameters(
        "2026-09-29T16:00:00", lambda _: FULL, action="hot_list"
    )

    assert chosen == {"action": "pool", "pool": "broken", "trade_date": "2026-09-28"}
    assert named == {"action": "reasons", "trade_date": "2026-09-26"}
    assert hot == {"action": "hot_list"}
