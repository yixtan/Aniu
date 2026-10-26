"""Enforce declared limits before a buy can reach the simulator.

A separate lock database serializes declarations and buy preflights across
processes without blocking the application database's worker heartbeats.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.account import (
    AccountSnapshot,
    PortfolioOrderSnapshot,
    PositionSnapshot,
)
from backend.business.exposure import ExposureCap
from backend.business.shared.trading.value_objects import utc_now
from backend.infra.integrations.trade_mutex import trade_mutex
from backend.infra.repositories.exposure_cap_repo import ExposureCapRepository


@asynccontextmanager
async def locked_cap_repository(
    factory: async_sessionmaker[AsyncSession], run_id: int
) -> AsyncIterator[tuple[ExposureCapRepository, float | None]]:
    async with trade_mutex(factory), factory() as session:
        repo = ExposureCapRepository(session)
        day, daily_limit = await repo.daily_limit_for_run(run_id)
        if day != utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date():
            raise ValueError("运行已跨交易日，禁止沿用旧声明；请等待当日新运行")
        yield repo, daily_limit
        await session.commit()


def check_declaration(cap: ExposureCap, daily_limit: float | None) -> None:
    if daily_limit is not None and cap.cap_pct > daily_limit:
        raise ValueError(
            f"同日重跑不能提高总敞口上限：当日有效上限 {daily_limit:g}%，"
            f"拒绝声明 {cap.cap_pct:g}%。维持或下调上限；新增候选只登记候补，"
            "升档等待下一交易日首轮重新确认。"
        )


def check_buy_exposure(
    snapshot: AccountSnapshot,
    orders: list[PortfolioOrderSnapshot],
    *,
    price: float,
    quantity: int,
    limit: float,
) -> None:
    if (
        not math.isfinite(snapshot.total_asset)
        or snapshot.total_asset <= 0
        or not math.isfinite(snapshot.market_value)
        or snapshot.market_value < 0
        or not math.isfinite(snapshot.frozen_cash)
        or snapshot.frozen_cash < 0
    ):
        raise ValueError("账户估值无效，无法核对总敞口，禁止新增买入")
    pending = sum(amount for _, amount in pending_buy_amounts(orders))
    # Frozen funds also cover orders not yet visible in the list. Use the larger
    # amount, not the sum, to avoid counting the same pending order twice.
    reserved = max(pending, snapshot.frozen_cash)
    projected = snapshot.market_value + reserved + price * quantity
    ceiling = snapshot.total_asset * limit / 100
    if not math.isfinite(projected) or projected > ceiling + 0.005:
        raise ValueError(
            f"买入后潜在总敞口 {projected:.2f} 元超过当日上限 {limit:g}%"
            f"（{ceiling:.2f} 元）；已计入持仓、未成交买单及本笔委托。"
            "不提高上限、不追买；新增候选登记候补与 forgone。"
        )


def check_same_day_chase(
    orders: list[PortfolioOrderSnapshot],
    *,
    started_at: datetime,
    symbol: str,
    price: float,
) -> None:
    """A later run cannot create a shallower buy leg above today's earlier bid.

    Compare only prior-run orders. Multiple levels planned within the first
    run remain possible. Cancelling an earlier bid does not erase its anchor.
    """
    shanghai = ZoneInfo("Asia/Shanghai")
    for order in orders:
        if (
            order.symbol != symbol
            or order.direction.upper() != "BUY"
            or order.status == "REJECTED"
        ):
            continue
        submitted = order.submitted_at
        reference = order.order_price or order.filled_price
        if submitted is None or submitted.tzinfo is None or reference is None:
            raise ValueError(
                "同标的历史买单缺少时间或价格，无法核对同日追买，禁止新增买入"
            )
        if not math.isfinite(reference) or reference <= 0:
            raise ValueError("同标的历史买单价格无效，禁止新增买入")
        if (
            submitted.astimezone(shanghai).date()
            != started_at.astimezone(shanghai).date()
        ):
            continue
        if submitted < started_at and price > reference + 0.000001:
            raise ValueError(
                f"同日重跑不得高价追买：{symbol} 此前买单价 {reference:g}，"
                f"本笔 {price:g}。不得取消深档后补建浅档；候选登记候补，等待跨日确认。"
            )


_TERMINAL_ORDER_STATUSES = frozenset(
    {"FILLED", "CANCELLED", "PARTIAL_CANCELLED", "REJECTED"}
)


def pending_buy_amounts(
    orders: list[PortfolioOrderSnapshot],
) -> list[tuple[str, float]]:
    """Only known sells and confirmed terminal orders can be excluded."""
    amounts = []
    for order in orders:
        remaining = order.quantity - order.filled_quantity
        if remaining < 0:
            raise ValueError("委托成交数量异常，禁止新增买入")
        if remaining == 0 or order.status in _TERMINAL_ORDER_STATUSES:
            continue
        direction = order.direction.upper()
        if direction == "SELL":
            continue
        if direction != "BUY":
            raise ValueError("未完成委托方向不明，禁止新增买入")
        if (
            order.status
            not in {"PENDING", "PARTIAL", "PENDING_CANCEL", "PARTIAL_PENDING_CANCEL"}
            or order.order_price is None
            or not math.isfinite(order.order_price)
            or order.order_price <= 0
        ):
            raise ValueError("未完成买单状态或价格不明，禁止新增买入")
        amounts.append((order.symbol, remaining * order.order_price))
    return amounts


def check_position_limits(
    snapshot: AccountSnapshot,
    positions: list[PositionSnapshot],
    orders: list[PortfolioOrderSnapshot],
    *,
    symbol: str,
    price: float,
    quantity: int,
) -> None:
    values: dict[str, float] = {}
    for position in positions:
        if not math.isfinite(position.market_value) or position.market_value < 0:
            raise ValueError("持仓估值无效，禁止新增买入")
        if position.quantity > 0:
            values[position.symbol] = (
                values.get(position.symbol, 0) + position.market_value
            )
    if snapshot.market_value > 0 and not values:
        raise ValueError("账户有持仓市值但缺少持仓明细，禁止新增买入")
    for code, amount in pending_buy_amounts(orders):
        values[code] = values.get(code, 0) + amount
    values[symbol] = values.get(symbol, 0) + price * quantity
    if values[symbol] > snapshot.total_asset * 0.10 + 0.005:
        raise ValueError("本笔买入后单票持仓加在挂买单超过总资产10%，禁止新增买入")
    if len(values) > 10:
        raise ValueError("持仓加在挂买单合计超过10只，禁止新增买入")
