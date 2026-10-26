"""Every live order needs a valid decision from this analysis, not old authority."""

from dataclasses import replace

import pytest

from backend.business.account import PortfolioOrderSnapshot
from backend.business.order_directives import (
    DirectiveAction,
    OrderDirective,
    RepricePlan,
)
from backend.business.order_directives.handoff import check_handoff

ORDER = PortfolioOrderSnapshot(
    order_id="test-order",
    symbol="600001",
    stock_name="合成股票",
    direction="BUY",
    quantity=100,
    status="PENDING",
    filled_quantity=0,
    filled_price=None,
)
PLAN = OrderDirective(
    order_id="test-order",
    symbol="600001",
    stock_name="合成股票",
    action=DirectiveAction.HOLD,
    note="保留",
    issued_by_run_id=1,
)


@pytest.mark.parametrize(
    "plans",
    [
        (),
        (replace(PLAN, issued_by_run_id=2),),
        (replace(PLAN, symbol="600002"),),
        (PLAN, PLAN),
        (replace(PLAN, rejected_reason="invalid"),),
        (replace(PLAN, cancel_if_price_above=float("nan")),),
        (
            replace(
                PLAN,
                action=DirectiveAction.REPRICE,
                reprice=RepricePlan(new_price=10, when_price_above=11),
            ),
        ),
    ],
)
def test_missing_old_duplicate_or_unexecutable_plans_are_incomplete(plans):
    result = check_handoff(1, [ORDER], plans)
    assert result.status == "incomplete"
    assert result.covered_count == 0
    assert result.issues


@pytest.mark.parametrize(
    "status", ["PENDING", "PARTIAL", "PENDING_CANCEL", "PARTIAL_PENDING_CANCEL"]
)
def test_remaining_orders_including_cancelling_require_plan(status):
    result = check_handoff(1, [replace(ORDER, status=status, filled_quantity=20)], ())
    assert result.status == "incomplete"
    assert result.active_count == 1


def test_hold_is_a_valid_explicit_decision_and_terminal_orders_do_not_need_plan():
    result = check_handoff(
        1, [ORDER, replace(ORDER, order_id="terminal", status="REJECTED")], (PLAN,)
    )
    assert result.status == "complete"
    assert result.active_count == result.covered_count == 1


def test_empty_account_is_complete_but_unknown_order_is_not():
    assert check_handoff(1, [], ()).status == "complete"
    assert (
        check_handoff(1, [replace(ORDER, status="UNKNOWN")], (PLAN,)).status
        == "unknown"
    )
