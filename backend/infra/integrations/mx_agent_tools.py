"""Direct Agent tools for MX research, portfolio, and paper trading."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field

from backend.agent.tools.registry import ToolRegistry
from backend.business.account import PortfolioOrderSnapshot
from backend.infra.integrations.tool_policy import SideEffectLevel
from backend.llm import ToolDefinition
from backend.stock_api import (
    MxMoniClient,
    MxPaperTradingClient,
    MxResearchClient,
    json_safe,
)
from backend.stock_api.mx.trading_parser import (
    ParsedTradeIntent,
    parse_trade_instruction,
)

READ_STAGES = ("Run",)
TRADE_STAGES = ("Run",)
# The order watch is deliberately not given the whole read set. It acts on a
# plan a run already wrote, so it needs to see the orders and nothing else:
# every tool it can reach is one it could be tempted to form a view with, and
# forming views is the job it does not have.
WATCH_READ_STAGES = ("Run", "Watch")
# Cancelling is undoing a decision that has already expired, so the watch may
# do it. Placing is making one, so it may not — a re-price reaches `trade`
# only through an authorization the run wrote down.
WATCH_TRADE_STAGES = ("Run", "Watch")
_DEFAULT_PORTFOLIO_ORDER_RESULT_LIMIT = 50
_PORTFOLIO_INTENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("positions", ("持仓", "positions", "position")),
    (
        "orders",
        (
            "委托",
            "订单",
            "orders",
            "order",
            "成交",
            "deal",
            "trade",
            "fill",
            "在途",
            "撤单",
            "废单",
        ),
    ),
    ("balance", ("资金", "余额", "资产", "balance", "cash", "账户", "bal")),
)


def _validated_order_result_options(limit: object, full: object) -> tuple[int, bool]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit 必须是大于 0 的整数")
    if not isinstance(full, bool):
        raise ValueError("full 必须是布尔值")
    return limit, full


@dataclass(slots=True)
class QueryMarketDataTool:
    client: MxResearchClient
    name: str = "query_market_data"
    enabled_stages: tuple[str, ...] = field(default=READ_STAGES)
    side_effect_level: SideEffectLevel = SideEffectLevel.READ
    execution_mode: str = "sequential"

    def to_tool_definition(self) -> ToolDefinition:
        return {
            "name": self.name,
            "description": (
                "用自然语言查询妙想提供的股票、指数、市场概况、财务和股权数据。"
                "不用于日、周、月或分钟 K 线；所有 K 线查询必须使用 query_kline。"
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1}},
                "required": ["query"],
                "additionalProperties": False,
            },
        }

    async def run(self, query: str) -> object:
        return await self.client.query_market_data(query)


@dataclass(slots=True)
class SearchNewsTool:
    client: MxResearchClient
    name: str = "search_news"
    enabled_stages: tuple[str, ...] = field(default=READ_STAGES)
    side_effect_level: SideEffectLevel = SideEffectLevel.READ
    execution_mode: str = "sequential"

    def to_tool_definition(self) -> ToolDefinition:
        return {
            "name": self.name,
            "description": "检索最新财经新闻、公告、研报和政策信息。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1}},
                "required": ["query"],
                "additionalProperties": False,
            },
        }

    async def run(self, query: str) -> object:
        return await self.client.search_news(query)


@dataclass(slots=True)
class SelectStocksTool:
    client: MxResearchClient
    name: str = "select_stocks"
    enabled_stages: tuple[str, ...] = field(default=READ_STAGES)
    side_effect_level: SideEffectLevel = SideEffectLevel.READ
    execution_mode: str = "parallel"

    def to_tool_definition(self) -> ToolDefinition:
        return {
            "name": self.name,
            "description": "按自然语言条件筛选 A 股或板块成分股。",
            "parameters": {
                "type": "object",
                "properties": {"keyword": {"type": "string", "minLength": 1}},
                "required": ["keyword"],
                "additionalProperties": False,
            },
        }

    async def run(self, keyword: str) -> object:
        return await self.client.select_stocks(keyword)


@dataclass(slots=True)
class QueryPortfolioTool:
    client: MxMoniClient
    name: str = "query_portfolio"
    enabled_stages: tuple[str, ...] = field(default=WATCH_READ_STAGES)
    side_effect_level: SideEffectLevel = SideEffectLevel.READ
    execution_mode: str = "parallel"

    def to_tool_definition(self) -> ToolDefinition:
        return {
            "name": self.name,
            "description": (
                "查询已绑定模拟组合的资金、持仓、委托、订单或成交。"
                "委托、订单与成交默认返回最后 50 条；limit 可指定最后 X 条；"
                "full=true 返回全量。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {"type": "string", "minLength": 1},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "default": _DEFAULT_PORTFOLIO_ORDER_RESULT_LIMIT,
                        "description": (
                            "委托、订单与成交返回最后 X 条；full=true 时忽略。"
                        ),
                    },
                    "full": {
                        "type": "boolean",
                        "default": False,
                        "description": "true 时委托、订单与成交返回全量。",
                    },
                },
                "required": ["instruction"],
                "additionalProperties": False,
            },
        }

    async def run(
        self,
        instruction: str,
        limit: int = _DEFAULT_PORTFOLIO_ORDER_RESULT_LIMIT,
        full: bool = False,
    ) -> object:
        order_limit, include_all_orders = _validated_order_result_options(limit, full)
        intents = _parse_portfolio_intents(instruction)

        async def load(intent: str) -> object:
            if intent == "balance":
                return json_safe(asdict(await self.client.get_account_snapshot()))
            if intent == "positions":
                return [
                    json_safe(asdict(item))
                    for item in await self.client.get_positions()
                ]
            if intent == "orders":
                return await self.client.get_orders()
            raise ValueError(f"unsupported portfolio intent: {intent}")

        values = await asyncio.gather(*(load(intent) for intent in intents))
        results = dict(zip(intents, values, strict=True))
        orders = results.get("orders")
        order_truncation: dict[str, int | bool] | None = None
        if isinstance(orders, list):
            total_orders = len(orders)
            selected_orders = orders if include_all_orders else orders[-order_limit:]
            results["orders"] = [json_safe(asdict(item)) for item in selected_orders]
            if len(selected_orders) < total_orders:
                order_truncation = {
                    "returned_count": len(selected_orders),
                    "total_count": total_orders,
                    "truncated": True,
                }
        payload: dict[str, object] = {
            "status": "ok",
            "query": instruction.strip(),
            "intents": list(intents),
        }
        if len(intents) == 1:
            payload["result"] = results[intents[0]]
        else:
            payload["results"] = results
        if order_truncation is not None:
            payload["truncation"] = {"orders": order_truncation}
        return payload


@dataclass(frozen=True, slots=True)
class UndoneOrder:
    """One order this run has cancelled, as it stood before the cancel."""

    symbol: str
    direction: str
    price: float
    quantity: int


class CancelledOrderLedger:
    """What this run has already undone, so it cannot quietly redo it.

    Scoped to a single run because that is the scope of a decision. Re-placing
    an order a *previous* run cancelled is a fresh judgement and may well be
    right; re-placing one this run cancelled, unchanged, is the run
    contradicting itself inside one sitting.

    That happened twice — 2026-09-14 14:00 on 002463 and 2026-09-15 10:50 on
    688008 — and both traces have the same shape: a thinking step that decides
    to cancel and re-price higher, the cancel, then a thinking step that starts
    over from the account balance, reaches the opposite conclusion, and
    restores the order at the identical price. The cause is that the model's
    reasoning is not replayed to it between tool calls, so each turn after a
    tool call begins from first principles.

    The refusal is deliberately a tool error rather than a silent drop: tool
    results *are* replayed, so the correction reaches the next turn even when
    the reasoning behind it does not. That is what makes this work regardless
    of how the channel is configured.
    """

    __slots__ = ("_undone",)

    def __init__(self) -> None:
        self._undone: set[tuple[str, str, float, int]] = set()

    @staticmethod
    def _key(symbol: str, direction: str, price: float, quantity: int) -> tuple[
        str, str, float, int
    ]:
        # A-share limit prices carry two decimals; rounding wider than that
        # keeps a float that arrived as 185.00000001 from reading as a new
        # price, without merging two genuinely different ones.
        return (symbol, direction.strip().lower(), round(price, 3), quantity)

    def record(self, orders: tuple[UndoneOrder, ...]) -> None:
        for order in orders:
            self._undone.add(
                self._key(order.symbol, order.direction, order.price, order.quantity)
            )

    def undone_match(
        self,
        *,
        symbol: str,
        direction: str,
        price: float,
        quantity: int,
    ) -> bool:
        """Whether this exact order was cancelled earlier in the same run.

        Exact on all four fields on purpose. Every legitimate follow-up to a
        cancel differs in price or size — that is what makes it a decision
        rather than an undo — so an exact match cannot block one.
        """

        return self._key(symbol, direction, price, quantity) in self._undone


@dataclass(slots=True)
class TradeTool:
    client: MxPaperTradingClient
    portfolio: MxMoniClient | None = None
    ledger: CancelledOrderLedger | None = None
    """Shared with the cancel tool. Without it the coherence guard is inert,
    which is why `register_mx_tools` always supplies one and a test pins that
    both tools receive the same instance."""
    name: str = "trade"
    enabled_stages: tuple[str, ...] = field(default=TRADE_STAGES)
    side_effect_level: SideEffectLevel = SideEffectLevel.WRITE
    execution_mode: str = "sequential"
    requires_market_open: bool = True

    def is_write_call(self, arguments: object) -> bool:
        del arguments
        return True

    def to_tool_definition(self) -> ToolDefinition:
        return {
            "name": self.name,
            "description": (
                "在已绑定的 MX A 股模拟组合中提交限价买卖委托。"
                "只接受格式“买入/卖出 <六位代码> <价格> <数量>”，"
                "例如“买入 600519 1700 100”。"
                "数量必须为 100 的整数倍；不支持市价委托。"
                "执行前系统会复核卖出持仓数量和买入可用资金。"
            ),
            "parameters": {
                "type": "object",
                "properties": {"instruction": {"type": "string", "minLength": 1}},
                "required": ["instruction"],
                "additionalProperties": False,
            },
        }

    async def run(self, instruction: str) -> object:
        intent = parse_trade_instruction(instruction)
        if intent.name != "trade":
            raise ValueError("trade only accepts a limit buy or sell instruction")
        _refuse_to_recreate_a_cancelled_order(self.ledger, intent)
        await _preflight_trade(self.portfolio, intent)
        return await self.client.trade(instruction)


@dataclass(slots=True)
class CancelTool:
    client: MxPaperTradingClient
    portfolio: MxMoniClient | None = None
    ledger: CancelledOrderLedger | None = None
    """Shared with the trade tool; see `TradeTool.ledger`."""
    name: str = "cancel"
    enabled_stages: tuple[str, ...] = field(default=WATCH_TRADE_STAGES)
    side_effect_level: SideEffectLevel = SideEffectLevel.WRITE
    execution_mode: str = "sequential"
    requires_market_open: bool = True
    requires_cancellable_session: bool = True
    """Cancelling stops three minutes before trading does.

    The closing call auction still accepts orders, so this is narrower than
    `requires_market_open` rather than a replacement for it.
    """

    def is_write_call(self, arguments: object) -> bool:
        del arguments
        return True

    def to_tool_definition(self) -> ToolDefinition:
        return {
            "name": self.name,
            "description": (
                "撤销模拟组合委托。指定撤单必须使用“撤单 <委托编号> <六位代码>”，"
                "例如“撤单 262154600000047682 515880”；"
                "需要撤销全部未成交委托时使用“一键撤单”。"
                "系统会先复核委托编号、股票代码和可撤状态。"
                "注意 14:57–15:00 是收盘集合竞价，交易所不接受撤单，"
                "此时段内撤单必定失败，重试也不会成功。"
            ),
            "parameters": {
                "type": "object",
                "properties": {"instruction": {"type": "string", "minLength": 1}},
                "required": ["instruction"],
                "additionalProperties": False,
            },
        }

    async def run(self, instruction: str) -> object:
        intent = parse_trade_instruction(instruction)
        if intent.name not in {"cancel", "cancel_all"}:
            raise ValueError("cancel only accepts a cancel instruction")
        about_to_undo = await _preflight_cancel(self.portfolio, intent)
        result = await self.client.cancel(instruction)
        # Recorded only after the provider accepted it: a cancel that failed
        # undid nothing, and must not stop the order being placed again.
        if self.ledger is not None:
            self.ledger.record(about_to_undo)
        return result


def register_mx_tools(
    registry: ToolRegistry,
    *,
    research: MxResearchClient,
    portfolio: MxMoniClient,
    trading: MxPaperTradingClient,
) -> None:
    """Expose every supported MX capability as a direct Agent tool."""

    registry.register(QueryMarketDataTool(research))
    registry.register(SearchNewsTool(research))
    registry.register(SelectStocksTool(research))
    registry.register(QueryPortfolioTool(portfolio))
    # One ledger, both tools, one run: the guard only means anything if the
    # tool that cancels and the tool that places are looking at the same book.
    ledger = CancelledOrderLedger()
    registry.register(TradeTool(trading, portfolio, ledger))
    registry.register(CancelTool(trading, portfolio, ledger))


async def _preflight_trade(
    portfolio: MxMoniClient | None,
    intent: ParsedTradeIntent,
) -> None:
    if portfolio is None:
        return
    payload = intent.payload
    stock_code = str(payload["stockCode"])
    quantity_value = payload["quantity"]
    assert isinstance(quantity_value, int)
    quantity = quantity_value
    direction = str(payload["type"])
    if direction == "buy":
        price_value = payload["price"]
        assert isinstance(price_value, (int, float))
        price = float(price_value)
        snapshot = await portfolio.get_account_snapshot()
        required_cash = price * quantity
        if required_cash > snapshot.available_cash:
            raise ValueError(
                f"买入所需资金 {required_cash:.2f} 元超过当前可用资金 "
                f"{snapshot.available_cash:.2f} 元"
            )
        return

    positions = await portfolio.get_positions()
    position = next((item for item in positions if item.symbol == stock_code), None)
    if position is None:
        raise ValueError(f"卖出数量 {quantity} 超过 {stock_code} 可卖数量 0")
    available = position.available_quantity
    if available is None:
        orders = await portfolio.get_orders()
        occupied = 0
        unknown_remaining = False
        pending_statuses = {
            "PENDING",
            "PARTIAL",
            "PARTIAL_PENDING_CANCEL",
            "PENDING_CANCEL",
        }
        for order in orders:
            if order.symbol != stock_code or order.direction != "SELL":
                continue
            remaining = max(order.quantity - order.filled_quantity, 0)
            if order.status in pending_statuses:
                occupied += remaining
            elif order.status == "UNKNOWN" and remaining > 0:
                unknown_remaining = True
        if unknown_remaining:
            raise ValueError(
                f"无法确认 {stock_code} 可卖数量；请求卖出 {quantity} 股，"
                "请先确认未完成卖出委托"
            )
        available = max(position.quantity - occupied, 0)
    if quantity > available:
        raise ValueError(f"卖出数量 {quantity} 超过 {stock_code} 可卖数量 {available}")


_CANCELLABLE_STATUSES = frozenset({"PENDING", "PARTIAL"})


def _undone_order(order: PortfolioOrderSnapshot) -> UndoneOrder | None:
    """Describe a cancelled order well enough to recognise its twin.

    An order whose price the provider did not report is not recorded: a guard
    that cannot compare prices would either miss the repeat or refuse a
    different one, and both are worse than staying quiet.
    """

    if order.order_price is None:
        return None
    return UndoneOrder(
        symbol=order.symbol,
        direction=order.direction,
        price=float(order.order_price),
        quantity=order.quantity,
    )


async def _preflight_cancel(
    portfolio: MxMoniClient | None,
    intent: ParsedTradeIntent,
) -> tuple[UndoneOrder, ...]:
    """Check the cancel is possible, and say what it is about to undo."""

    if portfolio is None:
        return ()
    if intent.name == "cancel_all":
        # Nothing to validate — the provider decides what "all" covers — but
        # the orders are still read, because a sweep undoes decisions just as
        # a named cancel does. A failure to read them costs the guard, not
        # the cancel.
        try:
            orders = await portfolio.get_orders()
        except Exception:
            return ()
        return tuple(
            undone
            for order in orders
            if order.status in _CANCELLABLE_STATUSES
            and (undone := _undone_order(order)) is not None
        )
    order_id = str(intent.payload["orderId"])
    stock_code = str(intent.payload["stockCode"])
    orders = await portfolio.get_orders()
    order = next((item for item in orders if item.order_id == order_id), None)
    if order is None:
        raise ValueError(f"未找到委托编号 {order_id}；请先查询委托或使用一键撤单")
    if order.symbol != stock_code:
        raise ValueError(
            f"委托编号 {order_id} 对应股票 {order.symbol}，与 {stock_code} 不一致"
        )
    if order.status not in _CANCELLABLE_STATUSES:
        raise ValueError(f"委托编号 {order_id} 当前状态为 {order.status}，不可撤销")
    undone = _undone_order(order)
    return () if undone is None else (undone,)


def _refuse_to_recreate_a_cancelled_order(
    ledger: CancelledOrderLedger | None,
    intent: ParsedTradeIntent,
) -> None:
    """Stop a run from restoring an order it cancelled moments ago.

    Raised as a tool error so the reason lands in the model's context, where
    the reasoning that led to the cancel does not.
    """

    if ledger is None:
        return
    payload = intent.payload
    price_value = payload["price"]
    quantity_value = payload["quantity"]
    if not isinstance(price_value, (int, float)) or not isinstance(
        quantity_value, int
    ):
        return
    symbol = str(payload["stockCode"])
    direction = str(payload["type"])
    if not ledger.undone_match(
        symbol=symbol,
        direction=direction,
        price=float(price_value),
        quantity=quantity_value,
    ):
        return
    side = "买入" if direction.strip().lower() == "buy" else "卖出"
    raise ValueError(
        f"本次运行刚撤销了同一笔委托（{symbol} {side} {quantity_value} 股 "
        f"@ {float(price_value):g}）。原价原量重挂等于这次撤单没有发生，"
        "只会把排队位置丢掉。若仍要成交请改价或改量；若不再需要就不要重挂，"
        "并在报告里写明撤单的理由。"
    )


def _parse_portfolio_intents(instruction: str) -> tuple[str, ...]:
    text = instruction.strip()
    if not text:
        raise ValueError("组合查询内容不能为空")
    lower = text.lower()
    intents = tuple(
        intent
        for intent, keywords in _PORTFOLIO_INTENTS
        if any(keyword in text or keyword in lower for keyword in keywords)
    )
    if not intents:
        raise ValueError("无法识别组合查询；支持资金、持仓、委托、订单和成交")
    return intents


__all__ = [
    "CancelTool",
    "QueryMarketDataTool",
    "QueryPortfolioTool",
    "SearchNewsTool",
    "SelectStocksTool",
    "TradeTool",
    "register_mx_tools",
]
