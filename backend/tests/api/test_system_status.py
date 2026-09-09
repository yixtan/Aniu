"""API tests for the system-status panel: real rows in, one folded day out."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient

from backend.infra.db.models import (
    MemoryActivityModel,
    MemoryDreamModel,
    MemoryItemModel,
    StockApiCallLogModel,
    StrategyRunModel,
    ToolInvocationModel,
)

RUN_ID = 20260909101
DREAM_ID = 20260908401


def _call_log(status: str, created_at: str) -> StockApiCallLogModel:
    return StockApiCallLogModel(
        source="public",
        provider="tencent",
        operation_id="stock_quote",
        parameters_json="{}",
        status=status,
        duration_ms=10,
        created_at=created_at,
    )


def _tool_call(call_id: str, tool: str, status: str, at: str) -> ToolInvocationModel:
    return ToolInvocationModel(
        run_id=RUN_ID,
        tool_call_id=call_id,
        tool_name=tool,
        arguments_json="{}",
        status=status,
        created_at=at,
    )


@pytest.mark.asyncio
async def test_system_status_folds_the_days_activity(
    api_client: AsyncClient, session
) -> None:
    # One moment for everything, so the row it lands in is known even if the
    # test runs across midnight.
    moment = datetime.now(tz=UTC) - timedelta(minutes=5)
    at = moment.isoformat()
    expected_day = moment.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()

    session.add(
        StrategyRunModel(
            id=RUN_ID,
            trigger_source="MANUAL",
            status="COMPLETED",
            current_state="Completed",
            snapshot_json={},
            trace_json={},
            summary_render_mode="html",
            total_tokens=1234,
            started_at=at,
            completed_at=at,
        )
    )
    await session.flush()
    session.add_all(
        [
            _tool_call("w1", "memory_write", "FAILED", at),
            _tool_call("w2", "memory_write", "COMPLETED", at),
            _tool_call("t1", "trade", "COMPLETED", at),
            # Died mid-call: no result to count either way.
            _tool_call("t2", "trade", "STARTED", at),
            _call_log("success", at),
            _call_log("failed", at),
            MemoryActivityModel(
                operation="read", task_id=RUN_ID, content="仓位 防守", created_at=at
            ),
            MemoryActivityModel(
                operation="read", task_id=RUN_ID, content="仓位 防守", created_at=at
            ),
            MemoryDreamModel(
                id=DREAM_ID,
                target_date="2026-09-08",
                status="completed",
                created_at=at,
                started_at=at,
                completed_at=at,
            ),
            MemoryActivityModel(
                operation="delete", task_id=DREAM_ID, memory_id=1, created_at=at
            ),
            MemoryActivityModel(
                operation="create", task_id=DREAM_ID, memory_id=2, created_at=at
            ),
            # The dream's own read must not count as a run asking memory.
            MemoryActivityModel(
                operation="read", task_id=DREAM_ID, content="纪律", created_at=at
            ),
            MemoryItemModel(
                content="consolidated",
                created_task_id=DREAM_ID,
                updated_task_id=DREAM_ID,
                replaces_json="[1]",
            ),
            MemoryItemModel(
                content="plain", created_task_id=RUN_ID, updated_task_id=RUN_ID
            ),
            MemoryItemModel(
                content="gone",
                created_task_id=RUN_ID,
                updated_task_id=RUN_ID,
                deleted_at=at,
            ),
        ]
    )
    await session.commit()

    response = await api_client.get("/api/aniu/system-status")

    assert response.status_code == 200
    body = response.json()
    assert len(body["days"]) == 7
    assert len(body["tokens"]) == 30

    today = next(row for row in body["days"] if row["day"] == expected_day)
    assert today == {
        "day": expected_day,
        "runs_completed": 1,
        "runs_failed": 0,
        "summaries_html": 1,
        "tokens": 1234,
        "trades_completed": 1,
        "trades_failed": 0,
        "memory_writes": 1,
        "memory_write_failures": 1,
        "memory_reads": 2,
        "memory_distinct_queries": 1,
        "data_calls": 2,
        "data_call_failures": 1,
    }
    token_row = next(row for row in body["tokens"] if row["day"] == expected_day)
    assert token_row == {"day": expected_day, "tokens": 1234, "runs": 1}

    assert len(body["dreams"]) == 1
    dream = body["dreams"][0]
    assert dream["target_date"] == "2026-09-08"
    assert dream["status"] == "completed"
    assert dream["failure_reason"] is None
    assert (dream["created"], dream["updated"], dream["deleted"]) == (1, 0, 1)
    assert (
        body["memory_live"],
        body["memory_deleted"],
        body["memory_with_lineage"],
    ) == (2, 1, 1)


@pytest.mark.asyncio
async def test_system_status_with_nothing_recorded_is_all_zeros(
    api_client: AsyncClient,
) -> None:
    response = await api_client.get("/api/aniu/system-status")

    assert response.status_code == 200
    body = response.json()
    assert all(row["runs_completed"] == 0 for row in body["days"])
    assert body["dreams"] == []
    assert (
        body["memory_live"],
        body["memory_deleted"],
        body["memory_with_lineage"],
    ) == (0, 0, 0)
