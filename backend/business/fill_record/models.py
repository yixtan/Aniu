"""What one run placed, and how the account has fared day by day."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

FILLED = "FILLED"


@dataclass(frozen=True, slots=True)
class AttributedOrder:
    """One order, and the run that placed it when that is knowable.

    `run_id` is None for orders placed before tool invocations were recorded.
    That is left as "unknown" rather than guessed from timing: an order and a
    run that merely overlap in time are not the same fact, and a wrong owner
    is worse here than a missing one — the whole point of this record is to
    stop a reader attributing one run's orders to another.
    """

    order_id: str
    run_id: int | None
    symbol: str
    stock_name: str
    direction: str
    quantity: int
    order_price: float | None
    status: str
    submitted_at: datetime | None

    @property
    def filled(self) -> bool:
        return self.status == FILLED


@dataclass(frozen=True, slots=True)
class DayTotals:
    """Every order submitted on one day, whichever run placed it."""

    day: date
    placing_runs: int
    ordered: int
    filled: int

    @property
    def fill_rate(self) -> float:
        return 0.0 if self.ordered == 0 else self.filled / self.ordered


@dataclass(frozen=True, slots=True)
class FillRecord:
    """The two blocks a reader needs, kept apart on purpose.

    Handing over one combined table is what let a reader treat a day's total
    as one run's own orders and then ask why the run's report named fewer.
    The day's total is the trend; `own_orders` is what this run did.
    """

    run_id: int
    own_orders: tuple[AttributedOrder, ...]
    days: tuple[DayTotals, ...]
    unattributed: int

    @property
    def own_filled(self) -> int:
        return sum(order.filled for order in self.own_orders)


__all__ = ["FILLED", "AttributedOrder", "DayTotals", "FillRecord"]
