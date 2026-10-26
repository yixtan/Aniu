"""Trusted handoff adapter refreshes all orders and detects ambiguous writes."""

from unittest.mock import AsyncMock, Mock

import pytest

from backend.infra.db.models import StrategyRunModel, ToolInvocationModel
from backend.infra.integrations.run_order_plan import RunOrderPlanQuery


@pytest.mark.asyncio
async def test_fresh_empty_account_does_not_hide_unresolved_trade(session_factory):
    async with session_factory() as session:
        session.add(
            StrategyRunModel(
                id=1,
                trigger_source="manual",
                status="RUNNING",
                current_state="Run",
                snapshot_json={},
                trace_json={},
                started_at="2026-10-26T01:00:00+00:00",
            )
        )
        session.add(
            ToolInvocationModel(
                run_id=1,
                tool_call_id="call",
                tool_name="trade",
                arguments_json="{}",
                status="STARTED",
            )
        )
        await session.commit()
    portfolio = AsyncMock()
    portfolio.clear_read_cache = Mock()
    portfolio.get_orders.return_value = []
    report = await RunOrderPlanQuery(session_factory, portfolio).check_handoff(1)
    assert report.status == "unknown"
    portfolio.clear_read_cache.assert_called_once()
    portfolio.get_orders.assert_awaited_once_with()
