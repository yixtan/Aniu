"""A directive is authority handed forward, so unreadable ones must not act."""

from __future__ import annotations

from datetime import time

import pytest

from backend.business.order_directives import (
    DirectiveAction,
    DirectiveKeyError,
    OrderDirective,
    RepricePlan,
    parse_directive,
    parse_directives,
)

RUN_ID = 20260912101


def _entry(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "order_id": "262534700000036039",
        "symbol": "601869",
        "stock_name": "长飞光纤",
        "action": "hold",
        "note": "筹码分散，但 435 以下仍有赔率",
    }
    payload.update(overrides)
    return payload


def test_a_directive_with_no_order_id_cannot_be_filed() -> None:
    with pytest.raises(DirectiveKeyError):
        parse_directive({"action": "cancel", "note": "放弃"}, run_id=RUN_ID)


def test_an_unknown_action_is_kept_as_an_inert_hold() -> None:
    directive = parse_directive(_entry(action="撤掉"), run_id=RUN_ID)

    assert directive.action is DirectiveAction.HOLD
    assert "撤掉" in directive.rejected_reason
    # Nothing about it may move the order, and with no conditions it never will.
    assert not directive.cancels_at(price=999.0, moment=time(14, 59))


def test_a_reprice_without_a_plan_is_kept_as_an_inert_hold() -> None:
    directive = parse_directive(_entry(action="reprice"), run_id=RUN_ID)

    assert directive.action is DirectiveAction.HOLD
    assert directive.rejected_reason == "reprice action needs a reprice object"
    assert directive.reprice is None


def test_a_malformed_price_downgrades_the_whole_directive() -> None:
    directive = parse_directive(
        _entry(cancel_if_price_above="四百四十五"), run_id=RUN_ID
    )

    assert directive.action is DirectiveAction.HOLD
    assert directive.rejected_reason == "cancel_if_price_above must be a number"
    assert directive.cancel_if_price_above is None


def test_one_bad_entry_does_not_take_the_others_with_it() -> None:
    directives, problems = parse_directives(
        [
            _entry(order_id="A", action="cancel", note="放弃，不追"),
            {"action": "cancel", "note": "没有 order_id"},
            _entry(order_id="B", cancel_if_price_above=445.0),
        ],
        run_id=RUN_ID,
    )

    assert [item.order_id for item in directives] == ["A", "B"]
    assert problems == ["directive has no order_id"]


def test_the_last_word_on_an_order_is_the_one_that_stands() -> None:
    directives, _ = parse_directives(
        [
            _entry(order_id="A", action="hold", note="先留着"),
            _entry(order_id="A", action="cancel", note="改主意了，撤"),
        ],
        run_id=RUN_ID,
    )

    assert len(directives) == 1
    assert directives[0].action is DirectiveAction.CANCEL


def test_cancel_needs_no_condition_and_hold_without_one_never_fires() -> None:
    cancel = parse_directive(_entry(action="cancel"), run_id=RUN_ID)
    hold = parse_directive(_entry(), run_id=RUN_ID)

    assert cancel.cancels_at(price=None, moment=time(9, 31))
    # An order dies at the close anyway, so "leave it" needs no expression.
    assert not hold.cancels_at(price=10_000.0, moment=time(14, 59))


def test_a_price_condition_is_unmet_when_no_price_can_be_seen() -> None:
    """A buy order on a stock not yet held has no price in query_portfolio."""

    directive = parse_directive(_entry(cancel_if_price_above=445.0), run_id=RUN_ID)

    assert directive.cancels_at(price=445.5, moment=time(10, 0))
    assert not directive.cancels_at(price=444.0, moment=time(10, 0))
    # Unobservable must not read as permission to act.
    assert not directive.cancels_at(price=None, moment=time(10, 0))


def test_an_unfilled_deadline_fires_on_the_clock_alone() -> None:
    directive = parse_directive(
        _entry(cancel_if_unfilled_after="14:30"), run_id=RUN_ID
    )

    assert not directive.cancels_at(price=None, moment=time(14, 29))
    assert directive.cancels_at(price=None, moment=time(14, 30))


def test_a_reprice_plan_carries_every_bound_it_needs() -> None:
    directive = parse_directive(
        _entry(
            action="reprice",
            reprice={"when_price_above": 442.0, "new_price": 445.0, "max_times": 2},
        ),
        run_id=RUN_ID,
    )

    assert directive.action is DirectiveAction.REPRICE
    assert directive.rejected_reason == ""
    assert directive.reprices_left == 2
    plan = directive.reprice
    assert plan is not None
    assert plan.triggered_at(442.5)
    assert not plan.triggered_at(441.0)


def test_a_reprice_plan_must_state_when_it_applies() -> None:
    directive = parse_directive(
        _entry(action="reprice", reprice={"new_price": 445.0}), run_id=RUN_ID
    )

    assert directive.action is DirectiveAction.HOLD
    assert directive.rejected_reason == "a reprice plan needs a price trigger"


def test_reprices_left_falls_to_zero_once_the_budget_is_spent() -> None:
    directive = OrderDirective(
        order_id="A",
        symbol="601869",
        stock_name="长飞光纤",
        action=DirectiveAction.REPRICE,
        note="试验性追价",
        issued_by_run_id=RUN_ID,
        reprice=RepricePlan(new_price=445.0, max_times=1, when_price_above=442.0),
        repriced_times=1,
    )

    assert directive.reprices_left == 0
