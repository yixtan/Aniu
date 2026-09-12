"""Domain models for what a run decided about each of its resting orders.

A resting limit order is a decision that has not taken effect yet: it fills on
its own if the price reaches it, with nobody agreeing to it a second time. A
run that reverses its view and leaves the matching order in place has left an
expired conclusion holding executable authority. These directives are how a run
hands that authority forward explicitly, so a later, cheaper task can act on it
without forming a view of its own.

Every condition here has to be answerable from price, clock and filled quantity
alone. That is not a stylistic preference: the task that evaluates them is given
no research tools, so a condition it cannot mechanically settle is a condition
nobody settles.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum


class DirectiveAction(StrEnum):
    CANCEL = "cancel"
    """Withdraw on the next pass, with no condition to check first."""

    HOLD = "hold"
    """Leave it resting; withdraw only if one of the cancel conditions hits."""

    REPRICE = "reprice"
    """Withdraw and re-place at a stated price when the trigger hits."""


@dataclass(frozen=True, slots=True)
class RepricePlan:
    """Authority to chase a price, bounded on every axis that could run away.

    ``new_price`` is an absolute number rather than an expression, so acting on
    this plan involves no arithmetic and therefore no judgement. It doubles as
    the price bound: there is nothing to bound when the destination is already
    stated. ``max_times`` bounds the count, and the plan's lifetime is bounded
    by the next run replacing the whole list.
    """

    new_price: float
    max_times: int = 1
    when_price_above: float | None = None
    when_price_below: float | None = None

    def __post_init__(self) -> None:
        if self.new_price <= 0:
            raise ValueError("new_price must be > 0")
        if self.max_times < 1:
            raise ValueError("max_times must be >= 1")
        if self.when_price_above is None and self.when_price_below is None:
            raise ValueError("a reprice plan needs a price trigger")

    def triggered_at(self, price: float) -> bool:
        if self.when_price_above is not None and price > self.when_price_above:
            return True
        return self.when_price_below is not None and price < self.when_price_below


@dataclass(frozen=True, slots=True)
class OrderDirective:
    """One run's disposition of one resting order."""

    order_id: str
    symbol: str
    stock_name: str
    action: DirectiveAction
    note: str
    issued_by_run_id: int
    cancel_if_price_above: float | None = None
    cancel_if_price_below: float | None = None
    cancel_if_unfilled_after: time | None = None
    reprice: RepricePlan | None = None
    repriced_times: int = 0
    rejected_reason: str = ""
    """Why a malformed directive was downgraded, empty when it arrived intact.

    A rejected directive is kept as a HOLD rather than dropped. Both leave the
    order untouched, but only one of them tells you afterwards whether the run
    tried and got the shape wrong or never spoke about the order at all — and
    the failure this whole mechanism exists to catch is the silent kind.
    """

    issued_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.order_id:
            raise ValueError("order_id must not be empty")
        if self.action is DirectiveAction.REPRICE and self.reprice is None:
            raise ValueError("a reprice directive needs a reprice plan")
        if self.repriced_times < 0:
            raise ValueError("repriced_times must be >= 0")

    @property
    def reprices_left(self) -> int:
        if self.reprice is None:
            return 0
        return max(0, self.reprice.max_times - self.repriced_times)

    def cancels_at(self, *, price: float | None, moment: time) -> bool:
        """Whether the stated conditions call for withdrawing this order now.

        CANCEL says so unconditionally. HOLD with no conditions never does —
        an order dies at the close anyway, so "leave it" needs no expression.
        A price condition with no price available is not met: an unobservable
        condition must not be read as a licence to act.
        """

        if self.action is DirectiveAction.CANCEL:
            return True
        if self.action is not DirectiveAction.HOLD:
            return False
        if price is not None:
            if (
                self.cancel_if_price_above is not None
                and price > self.cancel_if_price_above
            ):
                return True
            if (
                self.cancel_if_price_below is not None
                and price < self.cancel_if_price_below
            ):
                return True
        return (
            self.cancel_if_unfilled_after is not None
            and moment >= self.cancel_if_unfilled_after
        )


__all__ = ["DirectiveAction", "OrderDirective", "RepricePlan"]
