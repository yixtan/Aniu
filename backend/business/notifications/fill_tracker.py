"""Detect newly filled order quantity from account order snapshots.

An accepted limit order may never fill, so fills cannot be observed from the
write tool's response. They only appear in the account order cache, which is
rebuilt on every refresh. This module diffs an incoming order list against the
quantity already announced so a refresh never re-notifies the same fill.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from backend.business.notifications.models import (
    TradeDirection,
    TradeEventKind,
    TradeNotificationEvent,
)

_DIRECTIONS: dict[str, TradeDirection] = {
    "BUY": TradeDirection.BUY,
    "SELL": TradeDirection.SELL,
}


def coerce_direction(value: str | None) -> TradeDirection | None:
    if value is None:
        return None
    return _DIRECTIONS.get(value.strip().upper())


@dataclass(frozen=True, slots=True)
class OrderFillObservation:
    """One order row as seen by an account refresh.

    Deliberately decoupled from the account feature's own snapshot type so the
    notification layer stays importable from it without a cycle.
    """

    order_id: str
    symbol: str
    stock_name: str
    direction: str
    status: str
    quantity: int
    filled_quantity: int
    order_price: float | None = None
    filled_price: float | None = None


@dataclass(frozen=True, slots=True)
class FillDetectionResult:
    events: tuple[TradeNotificationEvent, ...]
    watermarks: dict[str, int]
    """Filled quantity announced per order id, to persist for the next refresh."""


def detect_fill_events(
    observations: Iterable[OrderFillObservation],
    announced: Mapping[str, int],
    *,
    cold_start: bool = False,
) -> FillDetectionResult:
    """Return one event per order whose filled quantity grew since last time.

    Partial fills notify incrementally: each refresh reports only the quantity
    that is newly filled, and the watermark advances to the running total.

    ``cold_start`` records the current quantities as the baseline and announces
    nothing. Without it, the very first refresh after this feature is installed
    cannot tell "just filled" from "filled last week", and would push one
    notification per historical order in the upstream window.
    """

    events: list[TradeNotificationEvent] = []
    watermarks: dict[str, int] = {}
    for order in observations:
        if not order.order_id:
            continue
        previous = max(int(announced.get(order.order_id, 0)), 0)
        filled = max(order.filled_quantity, 0)
        watermarks[order.order_id] = max(previous, filled)
        if cold_start or filled <= previous:
            continue
        events.append(
            TradeNotificationEvent(
                kind=TradeEventKind.ORDER_FILLED,
                order_id=order.order_id,
                stock_code=order.symbol,
                stock_name=order.stock_name,
                direction=coerce_direction(order.direction),
                price=order.order_price,
                quantity=order.quantity,
                filled_quantity=filled - previous,
                filled_price=order.filled_price,
            )
        )
    return FillDetectionResult(events=tuple(events), watermarks=watermarks)


__all__ = [
    "FillDetectionResult",
    "OrderFillObservation",
    "coerce_direction",
    "detect_fill_events",
]
