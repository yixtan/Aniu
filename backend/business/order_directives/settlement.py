"""Turning a run's directives into the actions a later pass should take.

Everything here is a pure function of what was written down, what is resting,
what it costs right now and what time it is. That is deliberate: the task that
carries these out is given no research tools, so anything it cannot settle from
those four inputs is something nobody settles, and an order nobody settles
keeps its authority by default — which is the failure the directives exist to
stop.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import time
from enum import StrEnum

from backend.business.order_directives.models import DirectiveAction, OrderDirective


class SettlementKind(StrEnum):
    CANCEL = "cancel"
    REPRICE = "reprice"
    HOLD = "hold"

    VANISHED = "vanished"
    """The plan named an order that is no longer resting.

    Usually it filled, and the fill notification has already said so. The case
    worth keeping a name for is the other one: a re-price is a cancel and a
    place, and a pass that dies between them leaves the order withdrawn and
    never replaced. Nothing else would notice, so this is reported and the next
    run is expected to speak to it.
    """

    UNCOVERED = "uncovered"
    """A resting order the plan says nothing about.

    Never acted on. Silence is not permission, and the whole mechanism exists
    because a run once went quiet about an order it had already reconsidered.
    """


@dataclass(frozen=True, slots=True)
class Settlement:
    order_id: str
    kind: SettlementKind
    reason: str
    new_price: float | None = None

    @property
    def acts(self) -> bool:
        return self.kind in {SettlementKind.CANCEL, SettlementKind.REPRICE}

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "order_id": self.order_id,
            "kind": self.kind.value,
            "reason": self.reason,
        }
        if self.new_price is not None:
            payload["new_price"] = self.new_price
        return payload


def settle(
    directives: Sequence[OrderDirective],
    *,
    resting_order_ids: Sequence[str],
    prices: Mapping[str, float],
    moment: time,
) -> list[Settlement]:
    """Decide what to do about every order either side knows of.

    ``prices`` may be missing a symbol; a price condition that cannot be read
    is not met. An unobservable condition must never be taken as licence to
    act, because acting is the irreversible half.
    """

    resting = list(dict.fromkeys(resting_order_ids))
    resting_set = set(resting)
    settlements: list[Settlement] = []
    addressed: set[str] = set()

    for directive in directives:
        addressed.add(directive.order_id)
        if directive.order_id not in resting_set:
            settlements.append(
                Settlement(
                    order_id=directive.order_id,
                    kind=SettlementKind.VANISHED,
                    reason="计划提到这笔委托，但它已不在挂单中",
                )
            )
            continue
        settlements.append(
            _settle_one(directive, price=prices.get(directive.symbol), moment=moment)
        )

    settlements.extend(
        Settlement(
            order_id=order_id,
            kind=SettlementKind.UNCOVERED,
            reason="挂单中有这笔委托，但计划没有提到它",
        )
        for order_id in resting
        if order_id not in addressed
    )
    return settlements


def _settle_one(
    directive: OrderDirective, *, price: float | None, moment: time
) -> Settlement:
    if directive.cancels_at(price=price, moment=moment):
        return Settlement(
            order_id=directive.order_id,
            kind=SettlementKind.CANCEL,
            reason=_cancel_reason(directive, price=price, moment=moment),
        )

    plan = directive.reprice
    if directive.action is DirectiveAction.REPRICE and plan is not None:
        if price is None:
            return Settlement(
                order_id=directive.order_id,
                kind=SettlementKind.HOLD,
                reason="改价条件需要现价，但现在读不到",
            )
        if not plan.triggered_at(price):
            return Settlement(
                order_id=directive.order_id,
                kind=SettlementKind.HOLD,
                reason=f"改价条件未触发（现价 {price}）",
            )
        if directive.reprices_left <= 0:
            return Settlement(
                order_id=directive.order_id,
                kind=SettlementKind.HOLD,
                reason=f"改价次数已用完（上限 {plan.max_times} 次）",
            )
        return Settlement(
            order_id=directive.order_id,
            kind=SettlementKind.REPRICE,
            reason=f"改价条件触发（现价 {price}），改挂 {plan.new_price}",
            new_price=plan.new_price,
        )

    return Settlement(
        order_id=directive.order_id,
        kind=SettlementKind.HOLD,
        reason="条件未触发，按计划继续挂着",
    )


def _cancel_reason(
    directive: OrderDirective, *, price: float | None, moment: time
) -> str:
    if directive.action is DirectiveAction.CANCEL:
        return "计划要求撤销"
    above = directive.cancel_if_price_above
    below = directive.cancel_if_price_below
    if price is not None and above is not None and price > above:
        return f"现价 {price} 高于撤单线 {above}"
    if price is not None and below is not None and price < below:
        return f"现价 {price} 低于撤单线 {below}"
    return f"到 {directive.cancel_if_unfilled_after} 仍未成交"


__all__ = ["Settlement", "SettlementKind", "settle"]
