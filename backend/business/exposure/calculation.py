"""One exposure calculation shared by execution protection and reporting."""

from __future__ import annotations

import math
from dataclasses import dataclass

from backend.business.account import AccountSnapshot, PortfolioOrderSnapshot

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


@dataclass(frozen=True)
class ExposureAmounts:
    pending: float
    reserved: float
    potential: float
    potential_pct: float


def exposure_amounts(
    snapshot: AccountSnapshot, orders: list[PortfolioOrderSnapshot]
) -> ExposureAmounts:
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
    reserved = max(pending, snapshot.frozen_cash)
    potential = snapshot.market_value + reserved
    if not math.isfinite(potential):
        raise ValueError("潜在敞口无法确认")
    return ExposureAmounts(
        pending, reserved, potential, potential / snapshot.total_asset * 100
    )
