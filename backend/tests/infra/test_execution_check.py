"""Recovery distinguishes accepted writes from fills and preserves uncertainty."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from backend.infra.db.models import (
    AccountCacheStateModel,
    AccountOrderCacheModel,
    StrategyRunModel,
    ToolInvocationModel,
)
from backend.infra.integrations.execution_check import ExecutionCheckQuery


async def seed(
    session,
    *,
    result=None,
    call_status="COMPLETED",
    order_status="PENDING",
    filled=0,
    cache_age=0,
    operation="trade",
    instruction="买入 600001 10 100",
):
    now = datetime.now(UTC)
    session.add(
        StrategyRunModel(
            id=1,
            trigger_source="manual",
            status="FAILED",
            current_state="Run",
            snapshot_json={},
            trace_json={"stages": [{"title": "研究交易", "status": "failed"}]},
            started_at=(now - timedelta(minutes=2)).isoformat(),
        )
    )
    session.add(
        ToolInvocationModel(
            run_id=1,
            tool_call_id="call",
            tool_name=operation,
            arguments_json=json.dumps({"instruction": instruction}),
            status=call_status,
            result_json=json.dumps(
                result
                if result is not None
                else {"code": "200", "data": {"orderID": "synthetic-order"}}
            ),
            created_at=(now - timedelta(seconds=60)).isoformat(),
            updated_at=now.isoformat(),
        )
    )
    session.add(
        AccountCacheStateModel(
            id=1, captured_at=(now - timedelta(seconds=cache_age)).isoformat()
        )
    )
    session.add(
        AccountOrderCacheModel(
            order_id="synthetic-order",
            symbol="600001",
            stock_name="合成股票",
            direction="BUY",
            quantity=100,
            order_price=10,
            status=order_status,
            filled_quantity=filled,
        )
    )
    await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,filled,outcome",
    [
        ("PENDING", 0, "accepted"),
        ("FILLED", 100, "filled"),
        ("PARTIAL", 30, "partial"),
        ("REJECTED", 0, "closed"),
    ],
)
async def test_failed_run_still_reports_actual_order_evidence(
    session, status, filled, outcome
):
    await seed(session, order_status=status, filled=filled)
    report = await ExecutionCheckQuery(session).check(1)
    assert report.interrupted
    assert "完整运行报告未生成" in report.unfinished_steps
    assert report.items[0].outcome == outcome
    assert report.items[0].filled_quantity == filled
    assert "uid" not in str(report)


@pytest.mark.asyncio
async def test_timeout_without_order_id_is_not_matched_by_stock_or_price(session):
    await seed(
        session,
        result={"error": "timeout"},
        call_status="FAILED",
        order_status="FILLED",
        filled=100,
    )
    item = (await ExecutionCheckQuery(session).check(1)).items[0]
    assert item.outcome == "unknown"
    assert item.filled_quantity is None


@pytest.mark.asyncio
async def test_account_snapshot_predating_call_cannot_confirm_fill(session):
    await seed(session, order_status="FILLED", filled=100, cache_age=600)
    report = await ExecutionCheckQuery(session).check(1)
    assert report.evidence_stale
    assert report.items[0].outcome == "accepted"
    assert report.items[0].filled_quantity is None


@pytest.mark.asyncio
async def test_cancel_accepted_response_is_not_confirmed_cancellation(session):
    await seed(session, operation="cancel", instruction="撤单 600001 synthetic-order")
    item = (await ExecutionCheckQuery(session).check(1)).items[0]
    assert item.outcome == "unknown"
    assert item.order_status == "PENDING"


@pytest.mark.asyncio
async def test_cancel_can_be_confirmed_by_account(session):
    await seed(
        session,
        operation="cancel",
        instruction="撤单 600001 synthetic-order",
        order_status="PARTIAL_CANCELLED",
        filled=20,
    )
    item = (await ExecutionCheckQuery(session).check(1)).items[0]
    assert item.outcome == "cancelled"
    assert item.filled_quantity == 20


async def test_cancel_uses_order_id_before_symbol_for_confirmation(session):
    await seed(
        session,
        operation="cancel",
        instruction="撤单 262154600000047682 600001",
        order_status="CANCELLED",
    )
    from sqlalchemy import update

    await session.execute(
        update(AccountOrderCacheModel).values(order_id="262154600000047682")
    )
    await session.commit()
    report = await ExecutionCheckQuery(session).check(1)
    assert report.items[0].outcome == "cancelled"
