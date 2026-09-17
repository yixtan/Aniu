"""Attribution of placed orders back to the run that placed them."""

from __future__ import annotations

import json

import pytest

from backend.business.fill_record import assemble_fill_record
from backend.infra.db.models import (
    AccountOrderCacheModel,
    StrategyRunModel,
    ToolInvocationModel,
)
from backend.infra.repositories.fill_record_repo import FillRecordRepository

RUN_A = 20260917101
RUN_B = 20260917102


def _run(session, run_id: int) -> None:
    session.add(
        StrategyRunModel(
            id=run_id,
            trigger_source="SCHEDULED",
            status="COMPLETED",
            current_state="Completed",
            snapshot_json={},
            trace_json={},
            summary_render_mode="markdown",
            started_at="2026-09-17T01:30:00+00:00",
            completed_at="2026-09-17T01:40:00+00:00",
        )
    )


def _order(
    session,
    order_id: str,
    *,
    status: str = "PENDING",
    at: str = "01:37",
) -> None:
    session.add(
        AccountOrderCacheModel(
            order_id=order_id,
            symbol="300394",
            stock_name="天孚通信",
            direction="BUY",
            quantity=200,
            order_price=265.0,
            status=status,
            filled_quantity=200 if status == "FILLED" else 0,
            filled_price=265.0 if status == "FILLED" else None,
            submitted_at=f"2026-09-17T{at}:00+00:00",
            updated_at=f"2026-09-17T{at}:00+00:00",
        )
    )


def _trade(session, run_id: int, call_id: str, result: object) -> None:
    session.add(
        ToolInvocationModel(
            run_id=run_id,
            tool_call_id=call_id,
            tool_name="trade",
            arguments_json="{}",
            status="COMPLETED",
            result_json=None if result is None else json.dumps(result),
            created_at="2026-09-17T01:37:00+00:00",
            updated_at="2026-09-17T01:37:00+00:00",
        )
    )


def _broker_reply(order_id: str) -> dict[str, object]:
    """The shape the paper-trading broker actually returns."""

    return {
        "code": "200",
        "message": "成功",
        "data": {"result": {"status": 0}, "secCode": "300394", "orderID": order_id},
    }


@pytest.mark.asyncio
async def test_a_runs_own_orders_are_its_own_not_the_days(session) -> None:
    """The record must never let a day's total read as one run's orders.

    An evaluator handed a per-day total alongside a run's report asked why the
    report named five orders when "the day" had ten, and demanded the missing
    exposure be explained. There were no missing orders: the other five came
    from later runs. Attribution is what makes that question impossible.
    """

    _run(session, RUN_A)
    _run(session, RUN_B)
    _order(session, "a-1")
    _order(session, "a-2", status="FILLED")
    _order(session, "b-1", at="01:54")
    _trade(session, RUN_A, "c1", _broker_reply("a-1"))
    _trade(session, RUN_A, "c2", _broker_reply("a-2"))
    _trade(session, RUN_B, "c3", _broker_reply("b-1"))
    await session.commit()

    orders = await FillRecordRepository(session).attributed_orders()
    record = assemble_fill_record(RUN_A, orders)

    assert [order.order_id for order in record.own_orders] == ["a-1", "a-2"]
    assert record.own_filled == 1
    # The day still totals all three, and says how many runs placed them.
    assert [(day.ordered, day.filled, day.placing_runs) for day in record.days] == [
        (3, 1, 2)
    ]


@pytest.mark.asyncio
async def test_an_order_with_no_usable_reply_stays_unattributed(session) -> None:
    """Better an owner nobody knows than an owner guessed from the clock."""

    _run(session, RUN_A)
    _order(session, "good")
    _order(session, "broken")
    _order(session, "empty")
    _order(session, "wrong-shape")
    _trade(session, RUN_A, "c1", _broker_reply("good"))
    session.add(
        ToolInvocationModel(
            run_id=RUN_A,
            tool_call_id="c2",
            tool_name="trade",
            arguments_json="{}",
            status="COMPLETED",
            result_json="{not json at all",
            created_at="2026-09-17T01:37:00+00:00",
            updated_at="2026-09-17T01:37:00+00:00",
        )
    )
    _trade(session, RUN_A, "c3", None)
    _trade(session, RUN_A, "c4", {"code": "200", "data": "成功"})
    await session.commit()

    orders = await FillRecordRepository(session).attributed_orders()
    record = assemble_fill_record(RUN_A, orders)

    assert [order.order_id for order in record.own_orders] == ["good"]
    assert record.unattributed == 3


@pytest.mark.asyncio
async def test_a_refused_trade_call_owns_nothing(session) -> None:
    """A call that failed placed no order, so it claims none."""

    _run(session, RUN_A)
    _order(session, "a-1")
    session.add(
        ToolInvocationModel(
            run_id=RUN_A,
            tool_call_id="c1",
            tool_name="trade",
            arguments_json="{}",
            status="FAILED",
            result_json=json.dumps(_broker_reply("a-1")),
            created_at="2026-09-17T01:37:00+00:00",
            updated_at="2026-09-17T01:37:00+00:00",
        )
    )
    await session.commit()

    record = assemble_fill_record(
        RUN_A, await FillRecordRepository(session).attributed_orders()
    )

    assert record.own_orders == ()
    assert record.unattributed == 1
