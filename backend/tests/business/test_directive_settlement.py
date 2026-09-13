"""Settling a directive against what is actually resting, priced and timed."""

from __future__ import annotations

from datetime import time

from backend.business.order_directives import (
    DirectiveAction,
    OrderDirective,
    RepricePlan,
    SettlementKind,
    settle,
)

NOON = time(11, 0)


def _directive(**overrides: object) -> OrderDirective:
    fields: dict[str, object] = {
        "order_id": "262534700000036039",
        "symbol": "601869",
        "stock_name": "长飞光纤",
        "action": DirectiveAction.HOLD,
        "note": "筹码分散，但 435 以下仍有赔率",
        "issued_by_run_id": 20260911106,
    }
    fields.update(overrides)
    return OrderDirective(**fields)  # type: ignore[arg-type]


def _settle(directive: OrderDirective, **overrides: object):
    options: dict[str, object] = {
        "resting_order_ids": [directive.order_id],
        "prices": {directive.symbol: 440.0},
        "moment": NOON,
    }
    options.update(overrides)
    return settle([directive], **options)[0]  # type: ignore[arg-type]


def test_an_unconditional_cancel_needs_no_price_and_no_clock() -> None:
    result = _settle(_directive(action=DirectiveAction.CANCEL), prices={})

    assert result.kind is SettlementKind.CANCEL
    assert result.acts


def test_a_hold_with_nothing_stated_leaves_the_order_alone() -> None:
    """An order dies at the close anyway, so "leave it" needs no expression."""

    assert _settle(_directive()).kind is SettlementKind.HOLD


def test_a_price_line_is_crossed_or_it_is_not() -> None:
    above = _directive(cancel_if_price_above=435.0)

    assert _settle(above, prices={"601869": 440.0}).kind is SettlementKind.CANCEL
    assert _settle(above, prices={"601869": 430.0}).kind is SettlementKind.HOLD


def test_a_price_condition_with_no_price_is_not_met() -> None:
    """An unobservable condition must never read as licence to act.

    Acting is the irreversible half, so the tie goes to leaving it alone.
    """

    result = _settle(_directive(cancel_if_price_above=435.0), prices={})

    assert result.kind is SettlementKind.HOLD


def test_an_unfilled_deadline_settles_off_the_clock_alone() -> None:
    deadline = _directive(cancel_if_unfilled_after=time(14, 30))

    assert _settle(deadline, moment=time(14, 29), prices={}).kind is SettlementKind.HOLD
    assert (
        _settle(deadline, moment=time(14, 30), prices={}).kind is SettlementKind.CANCEL
    )


def test_a_reprice_fires_only_once_its_trigger_and_budget_agree() -> None:
    plan = RepricePlan(new_price=445.0, max_times=1, when_price_above=442.0)
    directive = _directive(action=DirectiveAction.REPRICE, reprice=plan)

    quiet = _settle(directive, prices={"601869": 441.0})
    assert quiet.kind is SettlementKind.HOLD

    fired = _settle(directive, prices={"601869": 443.0})
    assert fired.kind is SettlementKind.REPRICE
    assert fired.new_price == 445.0

    spent = _settle(
        _directive(action=DirectiveAction.REPRICE, reprice=plan, repriced_times=1),
        prices={"601869": 443.0},
    )
    assert spent.kind is SettlementKind.HOLD
    assert "次数已用完" in spent.reason


def test_an_order_the_plan_named_but_that_is_no_longer_resting() -> None:
    """Usually it filled. The case worth naming is a re-price that half ran.

    A re-price is a cancel and a place; a pass that dies between them leaves
    the order withdrawn and never replaced, and nothing else would notice.
    """

    result = _settle(_directive(action=DirectiveAction.CANCEL), resting_order_ids=[])

    assert result.kind is SettlementKind.VANISHED
    assert not result.acts


def test_a_resting_order_nobody_wrote_about_is_never_acted_on() -> None:
    """Silence is not permission — that is the failure this exists to catch."""

    results = settle(
        [],
        resting_order_ids=["262534700000036039"],
        prices={"601869": 440.0},
        moment=NOON,
    )

    assert [item.kind for item in results] == [SettlementKind.UNCOVERED]
    assert not results[0].acts


def test_every_order_on_either_side_is_accounted_for_exactly_once() -> None:
    """A settlement nobody produced is the silence the mechanism forbids."""

    directive = _directive()
    results = settle(
        [directive],
        resting_order_ids=[directive.order_id, "999", "999"],
        prices={"601869": 440.0},
        moment=NOON,
    )

    assert [item.order_id for item in results] == [directive.order_id, "999"]
