"""Source-backed prerequisites shared by review and actual buy execution."""

from __future__ import annotations

import asyncio
import math
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from backend.business.shared.trading import utc_now
from backend.infra.calendar import is_trading_day
from backend.llm import AbortSignal
from backend.stock_api.public import StockMarketDataService
from backend.stock_api.public.contracts import (
    FinancialsRequest,
    StockMoneyFlowHistoryRequest,
)
from backend.stock_api.public.normalizers.sina_money import HISTORY_BASIS

POLICY_VERSION = "complete-buy-review-v1"
MARKET_ZONE = ZoneInfo("Asia/Shanghai")


def finite_number(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{field}缺失或无效，完整审查未通过")
    return float(value)


def previous_sessions(today: date) -> list[str]:
    days = []
    probe = today
    for _ in range(20):
        probe -= timedelta(days=1)
        if is_trading_day(probe):
            days.append(probe.isoformat())
        if len(days) == 2:
            return days
    raise ValueError("无法确认最近两个完整交易日，禁止买入")


def _data(
    result: dict[str, object], symbol: str, now: datetime, *, flow: bool = False
) -> dict[str, Any]:
    meta = result.get("meta")
    data = result.get("data")
    if not isinstance(meta, dict) or meta.get("source") not in (
        {"eastmoney", "sina"} if flow else {"eastmoney"}
    ):
        raise ValueError("审查数据来源无法确认")
    sina_basis_only = (
        flow
        and meta.get("source") == "sina"
        and meta.get("warnings") == [HISTORY_BASIS]
    )
    if (meta.get("degraded") and not sina_basis_only) or meta.get("partial"):
        raise ValueError("审查数据降级或不完整，禁止买入")
    try:
        age = (now - datetime.fromisoformat(str(meta["fetched_at"]))).total_seconds()
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("审查数据检索时间无法确认") from exc
    if not -5 <= age <= 60:
        raise ValueError("审查数据检索时间过期")
    if not isinstance(data, dict) or data.get("symbol") != symbol:
        raise ValueError("审查数据与标的不一致")
    return data


async def research_data(
    market: StockMarketDataService,
    symbol: str,
    now: datetime,
    abort_signal: AbortSignal | None,
) -> dict[str, object]:
    """Reject missing days and mixed sources instead of counting today's flow."""
    flow_result, financial_result = await asyncio.gather(
        market.execute_fresh(
            StockMoneyFlowHistoryRequest(symbol, limit=10), abort_signal
        ),
        market.execute_fresh(
            FinancialsRequest(symbol, mode="quarterly", limit=2), abort_signal
        ),
    )
    code = StockMoneyFlowHistoryRequest(symbol).symbol
    now = utc_now()
    flow = _data(flow_result, code, now, flow=True)
    financials = _data(financial_result, code, now)
    dates = previous_sessions(now.astimezone(MARKET_ZONE).date())
    items = flow.get("items")
    if not isinstance(items, list):
        raise ValueError("缺少资金历史记录，禁止买入")
    selected = []
    for day in dates:
        matches = [
            row
            for row in items
            if isinstance(row, dict)
            and str(row.get("date") or row.get("time") or "") == day
        ]
        if len(matches) != 1:
            raise ValueError(f"{day}资金记录缺失或重复，禁止买入")
        amount = finite_number(matches[0].get("main_net_inflow"), "主力净流入")
        if amount <= 0:
            raise ValueError(f"资金确认未通过：{day}同来源主力净流入不为正")
        selected.append({"date": day, "main_net_inflow": amount})
    quarters = financials.get("items")
    if not isinstance(quarters, list) or len(quarters) < 2:
        raise ValueError("缺少两个季度的财务证据，禁止买入")
    if not all(isinstance(row, dict) for row in quarters):
        raise ValueError("季度财务格式无效")
    rows = sorted(
        quarters, key=lambda row: str(row.get("report_period")), reverse=True
    )[:2]
    periods = [date.fromisoformat(str(row.get("report_period"))) for row in rows]
    today = now.astimezone(MARKET_ZONE).date()
    if (today - periods[0]).days > 210:
        raise ValueError("最新季度财务报告过期，禁止买入")
    for period in periods:
        if (
            period.month not in {3, 6, 9, 12}
            or period.day != monthrange(period.year, period.month)[1]
        ):
            raise ValueError("财务报告期不是季度末，禁止买入")
        if period > today or (today - period).days > 300:
            raise ValueError("财务报告期过期或在未来，禁止买入")
    if (periods[0].year * 12 + periods[0].month) - (
        periods[1].year * 12 + periods[1].month
    ) != 3:
        raise ValueError("财务季度不连续，禁止买入")
    latest_profit = finite_number(rows[0].get("net_profit"), "当季净利润")
    earlier_profit = finite_number(rows[1].get("net_profit"), "前季净利润")
    latest_revenue = finite_number(rows[0].get("revenue"), "当季收入")
    earlier_revenue = finite_number(rows[1].get("revenue"), "前季收入")
    # Expose the adverse operating signal for explicit decision review; this
    # does not invent an additional strategy-wide sizing rule.
    profit_down = latest_profit < earlier_profit and latest_revenue >= earlier_revenue
    return {
        "flow_source": cast(dict[str, Any], flow_result["meta"])["source"],
        "financial_source": "eastmoney",
        "completed_day_flows": selected,
        "financials": rows,
        "profit_down_revenue_not_down": profit_down,
    }
