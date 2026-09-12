"""Turn what a model emitted into directives, failing towards inaction.

Every rejection here downgrades to HOLD rather than raising. A malformed
directive and a refused one both leave the order alone, so the safe reading is
always available; what would not be safe is letting a typo in one entry throw
away the entries around it, or letting a half-understood field authorise a
trade. The rejection reason travels with the directive so the mistake stays
visible instead of looking like the run never spoke.
"""

from __future__ import annotations

from datetime import time
from typing import Any

from backend.business.order_directives.models import (
    DirectiveAction,
    OrderDirective,
    RepricePlan,
)

_MAX_NOTE_CHARACTERS = 500


class DirectiveKeyError(ValueError):
    """Raised when an entry cannot be filed at all, having no order id."""


def _text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _price(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    if value <= 0:
        raise ValueError(f"{key} must be > 0")
    return float(value)


def _clock(payload: dict[str, Any], key: str) -> time | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a HH:MM string")
    try:
        hour, minute = (int(part) for part in value.strip().split(":", 1))
        return time(hour=hour, minute=minute)
    except ValueError as error:
        raise ValueError(f"{key} must be a HH:MM string") from error


def _reprice_plan(payload: dict[str, Any]) -> RepricePlan:
    raw = payload.get("reprice")
    if not isinstance(raw, dict):
        raise ValueError("reprice action needs a reprice object")
    new_price = _price(raw, "new_price")
    if new_price is None:
        raise ValueError("reprice needs new_price")
    max_times = raw.get("max_times", 1)
    if isinstance(max_times, bool) or not isinstance(max_times, int):
        raise ValueError("max_times must be an integer")
    return RepricePlan(
        new_price=new_price,
        max_times=max_times,
        when_price_above=_price(raw, "when_price_above"),
        when_price_below=_price(raw, "when_price_below"),
    )


def parse_directive(payload: dict[str, Any], *, run_id: int) -> OrderDirective:
    """Return one directive, downgraded to HOLD if any part of it is unusable.

    Raises ``DirectiveKeyError`` only when there is no order id, because an
    entry that cannot be filed against an order cannot be downgraded either.
    """

    order_id = _text(payload, "order_id")
    if not order_id:
        raise DirectiveKeyError("directive has no order_id")

    common: dict[str, Any] = {
        "order_id": order_id,
        "symbol": _text(payload, "symbol"),
        "stock_name": _text(payload, "stock_name"),
        "note": _text(payload, "note")[:_MAX_NOTE_CHARACTERS],
        "issued_by_run_id": run_id,
    }

    try:
        action = DirectiveAction(_text(payload, "action"))
    except ValueError:
        return OrderDirective(
            action=DirectiveAction.HOLD,
            rejected_reason=f"unknown action: {_text(payload, 'action') or '(empty)'}",
            **common,
        )

    try:
        reprice = _reprice_plan(payload) if action is DirectiveAction.REPRICE else None
        return OrderDirective(
            action=action,
            cancel_if_price_above=_price(payload, "cancel_if_price_above"),
            cancel_if_price_below=_price(payload, "cancel_if_price_below"),
            cancel_if_unfilled_after=_clock(payload, "cancel_if_unfilled_after"),
            reprice=reprice,
            **common,
        )
    except ValueError as error:
        return OrderDirective(
            action=DirectiveAction.HOLD, rejected_reason=str(error), **common
        )


def parse_directives(
    payloads: list[dict[str, Any]], *, run_id: int
) -> tuple[list[OrderDirective], list[str]]:
    """Parse a whole list, keeping the unfilable entries as reported problems.

    One order is addressed once: a later entry for the same order replaces an
    earlier one rather than both surviving, because two live directives for one
    order have no defined meaning and picking the newer is the only reading that
    matches how the rest of the list is produced.
    """

    directives: dict[str, OrderDirective] = {}
    problems: list[str] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            problems.append("directive entry is not an object")
            continue
        try:
            directive = parse_directive(payload, run_id=run_id)
        except DirectiveKeyError as error:
            problems.append(str(error))
            continue
        directives[directive.order_id] = directive
    return list(directives.values()), problems


__all__ = ["DirectiveKeyError", "parse_directive", "parse_directives"]
