"""Agent tools for per-stock signals and short-term market sentiment.

Two read-only Run tools that a task calls only when its judgement needs them.
The user asked for the running task to decide
(「需要的时候让任务自己来决定是否选用」), so no prompt tells it to.

Registered from `agent_runtime.build_tool_registry` rather than from
`register_public_stock_tools`: this module borrows that module's helpers, and
the import may only run one way.

Dates are resolved here and nowhere else. The contracts take explicit dates
so one date is one cache key, and stock_api may not import the trading
calendar. "Today" is today in Asia/Shanghai once it is a trading day and the
09:25 call auction has matched; before that the previous trading day. A
summary, 涨停归因 list or 龙虎榜 asked for too early comes back empty — 龙虎榜
is published around 17:00–18:00 — so an empty answer for a date the tool chose
is asked once more for the trading day before, and says so. A pool is not: 0
跌停 or 0 炸板 is a true reading on a trading day once the auction has matched,
and on any finished day. A date the model chose is never moved.

The clock is also why some answers carry a warning the normalizers cannot
write: today's pools during the session are a snapshot that moves until the
close, and a stock's 龙虎榜 for today before publication cannot say "not
listed".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import ClassVar

from backend.infra.calendar import (
    MARKET_TIMEZONE,
    is_trading_day,
    last_trading_day_on_or_before,
)
from backend.infra.integrations.public_stock_agent_tools import (
    READ_STAGES,
    _action_arguments,
    _build_request,
    _const,
    _enum_property,
    _integer_property,
    _one_of,
    _PublicStockTool,
    _schema,
    _symbol_property,
)
from backend.llm import AbortSignal, ToolDefinition
from backend.stock_api.public import (
    DragonTigerMarketRequest,
    DragonTigerStockRequest,
    HotListRequest,
    InvalidStockRequest,
    LiftScheduleRequest,
    LimitPoolRequest,
    LimitReasonsRequest,
    LimitSummaryRequest,
    MarginDetailRequest,
    PublicStockRequest,
    UnsupportedStockRequest,
)

SESSION_DATA_FROM = time(9, 25)
"""When today becomes the default day: the 09:25 call-auction match.

Not 09:15, when the auction opens. Until the match the 涨停, 炸板 and 跌停 pools
for today are empty while the 昨日涨停池 already lists yesterday's names, so a
summary defaulted to today then is not empty — nothing retries it — and reads
0 涨停 and a 晋级率 on pre-match prices instead of the previous trading day.
"""

MARKET_CLOSE = time(15, 0)
"""Before this, today's pools are a live snapshot rather than the close."""

DRAGON_TIGER_PUBLISHED_FROM = time(18, 0)
"""The day's 龙虎榜 comes out after the close, usually 17:00–18:00."""

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
_LIMIT_POOLS = ("limit_up", "broken", "limit_down", "previous_limit_up")
_DATED_ACTIONS = frozenset({"summary", "pool", "reasons", "dragon_tiger"})
_INTRADAY_REQUESTS: tuple[type[PublicStockRequest], ...] = (
    LimitSummaryRequest,
    LimitPoolRequest,
    LimitReasonsRequest,
)


def _market_now() -> datetime:
    return datetime.now(MARKET_TIMEZONE)


def _previous_trading_day(day: date) -> date:
    try:
        return last_trading_day_on_or_before(day - timedelta(days=1))
    except ValueError as exc:
        raise UnsupportedStockRequest(
            f"交易日历缺少 {day.year} 年前后的数据，请直接提供 trade_date。"
        ) from exc


def default_trade_date(moment: datetime) -> date:
    """The trading day whose data a caller who named no date means."""

    local = moment.astimezone(MARKET_TIMEZONE)
    today = local.date()
    if is_trading_day(today) and local.time() >= SESSION_DATA_FROM:
        return today
    return _previous_trading_day(today)


def _is_empty(result: object) -> bool:
    """No rows and every count zero: nothing to read, not a zero reading."""

    if not isinstance(result, dict):
        return False
    data = result.get("data")
    if not isinstance(data, dict):
        return False
    items = data.get("items")
    if not isinstance(items, list) or items:
        return False
    return all(
        not value
        for key, value in data.items()
        if (key == "count" or key.endswith("_count"))
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


def _add_warning(result: object, warning: str) -> object:
    if not isinstance(result, dict):
        return result
    meta = result.get("meta")
    if not isinstance(meta, dict):
        return result
    warnings = meta.get("warnings")
    meta["warnings"] = [*(warnings if isinstance(warnings, list) else []), warning]
    return result


def _set_meta(result: object, key: str, value: object) -> None:
    meta = result.get("meta") if isinstance(result, dict) else None
    if isinstance(meta, dict):
        meta[key] = value


def _data(result: object) -> dict[str, object] | None:
    data = result.get("data") if isinstance(result, dict) else None
    return data if isinstance(data, dict) else None


def _require(values: dict[str, object], names: tuple[str, ...], action: str) -> None:
    missing = [name for name in names if values.get(name) is None]
    if missing:
        raise InvalidStockRequest(f"{action} 必须提供参数：{'、'.join(missing)}。")


def _date_property(description: str) -> dict[str, object]:
    return {"type": "string", "pattern": _DATE_PATTERN, "description": description}


@dataclass(slots=True)
class _DatedPublicStockTool(_PublicStockTool):
    enabled_stages: tuple[str, ...] = field(default=READ_STAGES)
    now: Callable[[], datetime] = field(default=_market_now)
    """Injectable so tests can fix the moment; must return an aware time."""

    def _today(self) -> date:
        return self.now().astimezone(MARKET_TIMEZONE).date()

    def _reject_future(self, value: object) -> None:
        # Format errors are the contract's to report, in its own words.
        if not isinstance(value, str) or not re.fullmatch(_DATE_PATTERN, value):
            return
        try:
            requested = date.fromisoformat(value)
        except ValueError:
            return
        today = self._today()
        if requested > today:
            raise InvalidStockRequest(
                f"trade_date 不能晚于今天（{today.isoformat()}）。"
            )

    async def _dated(
        self,
        factory: Callable[..., PublicStockRequest],
        values: dict[str, object],
        abort_signal: AbortSignal | None,
    ) -> object:
        explicit = values.get("trade_date")
        if explicit is not None:
            self._reject_future(explicit)
            result = await self._execute(_build_request(factory, values), abort_signal)
            return self._mark_intraday(factory, result)
        chosen = default_trade_date(self.now())
        result = await self._execute(
            _build_request(factory, {**values, "trade_date": chosen.isoformat()}),
            abort_signal,
        )
        # A pool the tool chose is today after the match or a finished day,
        # and either way an empty one is the reading: 0 跌停 on a strong day.
        # Swapping in the day before would hand over that day's 跌停 list.
        if factory is LimitPoolRequest or not _is_empty(result):
            return self._mark_intraday(factory, result)
        earlier = _previous_trading_day(chosen)
        retried = await self._execute(
            _build_request(factory, {**values, "trade_date": earlier.isoformat()}),
            abort_signal,
        )
        _set_meta(retried, "trade_date_fallback_from", chosen.isoformat())
        return _add_warning(
            retried,
            f"{chosen.isoformat()} 的数据还没有出来，返回的是 {earlier.isoformat()}。",
        )

    def _mark_intraday(
        self, factory: Callable[..., PublicStockRequest], result: object
    ) -> object:
        """Say so when today's pools are read before the close.

        The bases describe what the numbers count; only the tool knows the
        moment, and at 10:00 a 炸板率 of 40% is a reading that the afternoon
        can still undo, not the day's.
        """

        if not (isinstance(factory, type) and issubclass(factory, _INTRADAY_REQUESTS)):
            return result
        data = _data(result)
        moment = self.now().astimezone(MARKET_TIMEZONE)
        today = moment.date()
        if (
            data is None
            or data.get("trade_date") != today.isoformat()
            or not is_trading_day(today)
            or moment.time() >= MARKET_CLOSE
        ):
            return result
        return _add_warning(
            result,
            f"{today.isoformat()} 的数据是北京时间 {moment:%H:%M} 的盘中快照："
            "涨停、炸板、跌停家数，炸板率、封单金额和晋级率收盘前都还会变，"
            "不能直接和往日的收盘数比较。",
        )


@dataclass(slots=True)
class StockSignalsTool(_DatedPublicStockTool):
    name: str = "stock_signals"

    when_unreachable: ClassVar[str] = (
        "个股资金面改用 query_market_data（例如「600487 融资余额」「600487 龙虎榜」）。"
    )
    # Every source here is a datacenter report, and a refusal from it is as
    # final as a dropped connection.
    unreachable_categories: ClassVar[frozenset[str | None]] = frozenset(
        {
            "network",
            "timeout",
            "rate_limited",
            "upstream_http",
            "invalid_response",
            "business_failure",
            None,
        }
    )

    def to_tool_definition(self) -> ToolDefinition:
        symbol = _symbol_property()
        return {
            "name": self.name,
            "description": (
                "个股资金面信号：龙虎榜（是否上榜、机构专用和沪深股通席位的买卖额）、"
                "限售解禁（过去批次，和今天起 90 天内的解禁）、融资融券（融资余额与每日"
                "融资净买入，T+1 公布）。在准备买入、解释异动或复核持仓风险时按需"
                "调用；与当前判断无关时不必调用。"
            ),
            "parameters": _one_of(
                [
                    _schema(
                        {
                            "action": _const("dragon_tiger"),
                            "symbol": symbol,
                            "limit": {
                                **_integer_property(1, 20),
                                "description": (
                                    "条数上限：dragon_tiger 默认 5、最多 20 次上榜；"
                                    "margin 默认 10、最多 60 个交易日。"
                                ),
                            },
                            "trade_date": _date_property(
                                "要看席位的上榜日期 yyyy-MM-dd；省略时看最近一次上榜。"
                                "当天的龙虎榜收盘后约 17:00–18:00 才公布，公布前查"
                                "当天查不到不代表没上榜。"
                            ),
                        },
                        ["action", "symbol"],
                    ),
                    _schema(
                        {"action": _const("lift"), "symbol": symbol},
                        ["action", "symbol"],
                    ),
                    _schema(
                        {
                            "action": _const("margin"),
                            "symbol": symbol,
                            "limit": _integer_property(1, 60),
                        },
                        ["action", "symbol"],
                    ),
                ]
            ),
        }

    async def run(self, action: str, **kwargs: object) -> object:
        return await self._run(action, kwargs, None)

    async def run_with_abort(
        self, action: str, *, abort_signal: AbortSignal | None, **kwargs: object
    ) -> object:
        return await self._run(action, kwargs, abort_signal)

    async def _run(
        self, action: str, kwargs: dict[str, object], abort_signal: AbortSignal | None
    ) -> object:
        request: PublicStockRequest
        if action == "dragon_tiger":
            values = _action_arguments(
                kwargs, {"symbol", "limit", "trade_date"}, "stock_signals.dragon_tiger"
            )
            _require(values, ("symbol",), "stock_signals.dragon_tiger")
            self._reject_future(values.get("trade_date"))
            result = await self._execute(
                _build_request(DragonTigerStockRequest, values), abort_signal
            )
            return self._hedge_unpublished(values.get("trade_date"), result)
        elif action == "lift":
            values = _action_arguments(kwargs, {"symbol"}, "stock_signals.lift")
            _require(values, ("symbol",), "stock_signals.lift")
            request = _build_request(
                LiftScheduleRequest, {**values, "as_of": self._today().isoformat()}
            )
        elif action == "margin":
            values = _action_arguments(
                kwargs, {"symbol", "limit"}, "stock_signals.margin"
            )
            _require(values, ("symbol",), "stock_signals.margin")
            request = _build_request(MarginDetailRequest, values)
        else:
            raise UnsupportedStockRequest(
                "stock_signals.action 必须为 dragon_tiger、lift 或 margin。"
            )
        return await self._execute(request, abort_signal)

    def _hedge_unpublished(self, trade_date: object, result: object) -> object:
        """Today's 龙虎榜 before publication: "not listed" is not known yet.

        The listings page cannot hold today's row before about 17:00–18:00,
        so the normalizer's 「没有上龙虎榜」 would be a flat false negative for
        a stock that hit the limit at 14:50.
        """

        moment = self.now().astimezone(MARKET_TIMEZONE)
        today = moment.date()
        data = _data(result)
        if (
            data is None
            or trade_date != today.isoformat()
            or not is_trading_day(today)
            or moment.time() >= DRAGON_TIGER_PUBLISHED_FROM
        ):
            return result
        items = data.get("items")
        listings = items if isinstance(items, list) else []
        if any(
            isinstance(item, dict) and item.get("date") == trade_date
            for item in listings
        ):
            return result
        pending = (
            f"{trade_date} 的龙虎榜收盘后约 17:00–18:00 才公布，现在查不到不代表"
            "没上榜，今天是否上榜现在还判断不了"
        )
        since = data.get("since")
        if listings:
            data["note"] = (
                f"{pending}；最近一次上榜见 items（省略 trade_date 可看它的席位）。"
            )
        elif isinstance(since, str):
            data["note"] = f"{pending}；在此之前，{since} 以来没有上过龙虎榜。"
        else:
            data["note"] = f"{pending}。"
        return _add_warning(result, f"{pending}。")


@dataclass(slots=True)
class MarketSentimentTool(_DatedPublicStockTool):
    name: str = "market_sentiment"

    when_unreachable: ClassVar[str] = (
        "情绪数据取不到时，可以用 select_stocks（例如「今日涨停的 A 股」）、"
        "query_market_data（例如「今日龙虎榜」）或 search_news 代替。"
    )
    # push2ex and 同花顺 answer an anti-bot filter's way — a 403, an HTML page,
    # a refusal code — as often as by hanging up.
    unreachable_categories: ClassVar[frozenset[str | None]] = (
        StockSignalsTool.unreachable_categories
    )

    def to_tool_definition(self) -> ToolDefinition:
        trade_date = _date_property(
            "交易日 yyyy-MM-dd，不能晚于今天。省略时取最近的交易日（当天 09:25 集合"
            "竞价撮合前取上一个交易日）；这样取到的日期还没有数据时（summary、"
            "reasons、dragon_tiger），自动改取上一个交易日并注明；pool 不改日期，"
            "空池就是当天 0 只。"
        )
        return {
            "name": self.name,
            "description": (
                "市场短线情绪：涨停、炸板、跌停家数与炸板率、连板高度与梯队、"
                "昨日涨停今日表现（summary）；四个涨跌停池明细（pool）；当日涨停股的"
                "题材归因（reasons，同花顺）；同花顺人气热榜（hot_list）；全市场当日"
                "龙虎榜（dragon_tiger，收盘后约 17:00–18:00 公布）。判断赚钱效应、"
                "主线题材和情绪冷暖时按需调用。"
            ),
            "parameters": _one_of(
                [
                    _schema(
                        {"action": _const("summary"), "trade_date": trade_date},
                        ["action"],
                    ),
                    _schema(
                        {
                            "action": _const("pool"),
                            "pool": {
                                **_enum_property(_LIMIT_POOLS),
                                "description": (
                                    "limit_up 涨停池、broken 炸板池、"
                                    "limit_down 跌停池、"
                                    "previous_limit_up 昨日涨停股的今日表现。"
                                ),
                            },
                            "trade_date": trade_date,
                            "limit": {
                                **_integer_property(1, 200),
                                "description": (
                                    "条数上限：pool、reasons、dragon_tiger 默认 50、"
                                    "最多 200；hot_list 默认 30、最多 100。"
                                ),
                            },
                        },
                        ["action", "pool"],
                    ),
                    _schema(
                        {
                            "action": _const("reasons"),
                            "trade_date": trade_date,
                            "limit": _integer_property(1, 200),
                        },
                        ["action"],
                    ),
                    _schema(
                        {
                            "action": _const("hot_list"),
                            "limit": _integer_property(1, 100),
                        },
                        ["action"],
                    ),
                    _schema(
                        {
                            "action": _const("dragon_tiger"),
                            "trade_date": trade_date,
                            "limit": _integer_property(1, 200),
                        },
                        ["action"],
                    ),
                ]
            ),
        }

    async def run(self, action: str, **kwargs: object) -> object:
        return await self._run(action, kwargs, None)

    async def run_with_abort(
        self, action: str, *, abort_signal: AbortSignal | None, **kwargs: object
    ) -> object:
        return await self._run(action, kwargs, abort_signal)

    def stock_api_log_result_parameters(
        self, parameters: dict[str, object], result: object
    ) -> dict[str, object]:
        """Put the day the tool chose into the call log, once it is known.

        The model's arguments carry no date when it named none, and a
        fallback moves the day after the call, so only the answer says which
        day was read.
        """

        if (
            parameters.get("trade_date") is not None
            or parameters.get("action") not in _DATED_ACTIONS
        ):
            return parameters
        data = _data(result)
        served = data.get("trade_date") if data is not None else None
        if not isinstance(served, str):
            return parameters
        enriched = {**parameters, "trade_date": served}
        meta = result.get("meta") if isinstance(result, dict) else None
        fallback_from = (
            meta.get("trade_date_fallback_from") if isinstance(meta, dict) else None
        )
        if isinstance(fallback_from, str):
            enriched["trade_date_fallback_from"] = fallback_from
        return enriched

    async def _run(
        self, action: str, kwargs: dict[str, object], abort_signal: AbortSignal | None
    ) -> object:
        factory: Callable[..., PublicStockRequest]
        if action == "summary":
            values = _action_arguments(
                kwargs, {"trade_date"}, "market_sentiment.summary"
            )
            factory = LimitSummaryRequest
        elif action == "pool":
            values = _action_arguments(
                kwargs, {"pool", "trade_date", "limit"}, "market_sentiment.pool"
            )
            _require(values, ("pool",), "market_sentiment.pool")
            factory = LimitPoolRequest
        elif action == "reasons":
            values = _action_arguments(
                kwargs, {"trade_date", "limit"}, "market_sentiment.reasons"
            )
            factory = LimitReasonsRequest
        elif action == "dragon_tiger":
            values = _action_arguments(
                kwargs, {"trade_date", "limit"}, "market_sentiment.dragon_tiger"
            )
            factory = DragonTigerMarketRequest
        elif action == "hot_list":
            values = _action_arguments(kwargs, {"limit"}, "market_sentiment.hot_list")
            return await self._execute(
                _build_request(HotListRequest, values), abort_signal
            )
        else:
            raise UnsupportedStockRequest(
                "market_sentiment.action 必须为 summary、pool、reasons、"
                "hot_list 或 dragon_tiger。"
            )
        return await self._dated(factory, values, abort_signal)


__all__ = [
    "DRAGON_TIGER_PUBLISHED_FROM",
    "MARKET_CLOSE",
    "MarketSentimentTool",
    "SESSION_DATA_FROM",
    "StockSignalsTool",
    "default_trade_date",
]
