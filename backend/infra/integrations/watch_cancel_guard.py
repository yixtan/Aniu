"""Validate the trusted Watch plan immediately before a cancellation."""

from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.business.order_directives import DirectiveAction, OrderDirective
from backend.business.shared.trading.value_objects import utc_now
from backend.llm import AbortSignal
from backend.stock_api.mx.trading_parser import parse_trade_instruction
from backend.stock_api.public import QuoteSnapshotRequest, StockMarketDataService


async def verify_watch_cancel(
    instruction: str,
    *,
    directives: tuple[OrderDirective, ...] | None,
    market_data: StockMarketDataService | None,
    abort_signal: AbortSignal | None,
) -> None:
    intent = parse_trade_instruction(instruction)
    if intent.name != "cancel" or directives is None:
        raise ValueError("盯盘缺少可信的逐笔撤单计划，已阻止写操作")
    matches = [
        item for item in directives if item.order_id == intent.payload["orderId"]
    ]
    if len(matches) != 1 or matches[0].symbol != intent.payload["stockCode"]:
        raise ValueError("委托编号或股票与盯盘计划不一致，已阻止撤单")
    directive = matches[0]
    if directive.rejected_reason:
        raise ValueError("该计划格式无效，不能授权撤单")
    if directive.action is DirectiveAction.REPRICE:
        raise ValueError("盯盘不能提交改价后的新委托，禁止只执行改价计划的撤单部分")
    thresholds = (directive.cancel_if_price_above, directive.cancel_if_price_below)
    if any(
        value is not None and (not math.isfinite(value) or value <= 0)
        for value in thresholds
    ):
        raise ValueError("撤单阈值无效，已阻止撤单")
    moment = utc_now().astimezone(ZoneInfo("Asia/Shanghai")).time()
    # Explicit CANCEL and elapsed time conditions need no price evidence.
    if directive.cancels_at(price=None, moment=moment):
        return
    if not any(value is not None for value in thresholds):
        raise ValueError("计划要求继续持有或时间条件尚未触发，已阻止撤单")
    if market_data is None:
        raise ValueError("缺少实时行情连接，无法验证撤单条件")
    request = QuoteSnapshotRequest((directive.symbol,))
    result = await market_data.execute_fresh(request, abort_signal)
    data = result.get("data")
    quotes = data.get("quotes") if isinstance(data, dict) else None
    if not isinstance(quotes, list):
        raise ValueError("行情缺少有效价格，已阻止撤单")
    rows = [
        row
        for row in quotes
        if isinstance(row, dict) and row.get("symbol") == request.symbols[0]
    ]
    if len(rows) != 1:
        raise ValueError("行情标的不匹配或重复，已阻止撤单")
    row = rows[0]
    price = row.get("price")
    if (
        isinstance(price, bool)
        or not isinstance(price, (int, float))
        or not math.isfinite(price)
        or price <= 0
    ):
        raise ValueError("实时价格无效，已阻止撤单")
    raw_time = row.get("market_time")
    try:
        observed = datetime.fromisoformat(str(raw_time))
    except ValueError as exc:
        raise ValueError("行情时间缺失，已阻止撤单") from exc
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    age = (utc_now() - observed).total_seconds()
    if age < -5 or age > 60:
        raise ValueError("行情时间过期或异常，已阻止撤单")
    moment = utc_now().astimezone(ZoneInfo("Asia/Shanghai")).time()
    if not directive.cancels_at(price=float(price), moment=moment):
        raise ValueError("计划的价格或时间撤单条件尚未触发，已阻止撤单")
