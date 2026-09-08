"""Tests for Run-stage memory tools."""

from __future__ import annotations

from datetime import date

import pytest

from backend.agent.kernel.runtime_config import LlmRuntimeConfig
from backend.agent.tools import ToolRegistry
from backend.business.memories import MemoryService
from backend.business.runs import StrategyRun, StrategySnapshot
from backend.business.runs.execution import RunExecutionContext
from backend.business.shared.enums import TriggerSource
from backend.infra.integrations.agent_runner import (
    AgentRunnerFactoryAdapter,
    _StageToolRegistry,
)
from backend.infra.integrations.agent_runtime import AgentRuntimeFactory
from backend.infra.integrations.dream_agent_tools import DreamReportReadTool
from backend.infra.integrations.memory_agent_tools import (
    ALL_MEMORY_OPERATIONS,
    AUTHORING_OPERATIONS,
    MemoryReadTool,
    MemoryWriteTool,
)
from backend.llm import ModelProtocol


@pytest.mark.asyncio
async def test_memory_tools_write_then_read_across_sessions(session_factory) -> None:
    writer = MemoryWriteTool(session_factory)
    created = await writer.run_for_call(
        run_id=200,
        tool_call_id="memory-write-1",
        operation="create",
        content="弱市缩量反弹时不要追高。",
        reason="本次运行观察到追高后回撤。",
    )

    assert created["status"] == "ok"
    assert created["item"]["version"] == 1
    reader = MemoryReadTool(session_factory)
    loaded = await reader.run(keywords="弱市 追高", limit=5)

    assert loaded["status"] == "ok"
    assert loaded["items"][0]["content"] == "弱市缩量反弹时不要追高。"
    assert loaded["items"][0]["reason"] == "本次运行观察到追高后回撤。"


@pytest.mark.asyncio
async def test_dream_report_tool_accepts_harness_metadata(session_factory) -> None:
    result = await DreamReportReadTool(session_factory, date(2026, 8, 18)).run_for_call(
        run_id=20260818401,
        tool_call_id="dream-report-1",
        offset=0,
        limit=10,
    )

    assert result["status"] == "ok"
    assert result["reports"] == []


@pytest.mark.asyncio
async def test_memory_read_schema_exposes_match_mode(session_factory) -> None:
    definition = MemoryReadTool(session_factory).to_tool_definition()
    parameters = definition["parameters"]

    assert set(parameters["properties"]) == {"keywords", "match_mode", "limit"}
    assert parameters["properties"]["match_mode"]["enum"] == ["and", "or"]
    assert parameters["required"] == ["keywords", "match_mode"]


@pytest.mark.asyncio
async def test_memory_write_supports_update_and_soft_delete(session_factory) -> None:
    writer = MemoryWriteTool(session_factory)
    created = await writer.run_for_call(
        run_id=200,
        tool_call_id="memory-write-1",
        operation="create",
        content="弱市不要追高。",
        reason="本次运行观察到追高后回撤。",
    )
    memory_id = created["item"]["id"]

    updated = await writer.run_for_call(
        run_id=201,
        tool_call_id="memory-write-2",
        operation="update",
        memory_id=memory_id,
        expected_version=1,
        content="弱市缩量反弹时不要追高。",
        reason="补充成交量确认条件。",
    )
    assert updated["item"]["version"] == 2

    deleted = await writer.run_for_call(
        run_id=202,
        tool_call_id="memory-write-3",
        operation="delete",
        memory_id=memory_id,
        expected_version=2,
    )
    assert deleted["item"]["version"] == 3
    assert deleted["item"]["deleted_at"] is not None


@pytest.mark.asyncio
async def test_memory_write_schema_matches_operation_requirements(
    session_factory,
) -> None:
    definition = MemoryWriteTool(session_factory).to_tool_definition()
    parameters = definition["parameters"]
    properties = parameters["properties"]

    # Every field the tool accepts has to be visible. A schema that hides them
    # behind branches reaches the model as an object with no fields, and it
    # then invents them.
    assert set(properties) == {
        "operation",
        "content",
        "reason",
        "memory_id",
        "expected_version",
        "replaces",
    }
    # Lineage is optional: an ordinary new memory replaces nothing.
    assert "replaces" not in parameters["required"]
    assert properties["operation"]["enum"] == ["create", "update", "delete"]
    # Only what every operation needs can be required of all of them.
    assert parameters["required"] == ["operation"]

    # What the flat schema cannot require, it has to say.
    hint = properties["operation"]["description"]
    assert "operation=create 时必填 content、reason" in hint
    assert (
        "operation=update 时必填 memory_id、expected_version、content、reason" in hint
    )
    assert "operation=delete 时必填 memory_id、expected_version" in hint


@pytest.mark.asyncio
async def test_memory_read_returns_payload_when_activity_audit_fails(
    session_factory, monkeypatch
) -> None:
    async def fail_record_read(*_: object, **__: object) -> object:
        raise RuntimeError("activity database is unavailable")

    monkeypatch.setattr(MemoryService, "record_read", fail_record_read)

    result = await MemoryReadTool(session_factory).run_for_call(
        run_id=301,
        tool_call_id="memory-read-audit-failure",
        keywords="不存在的记忆",
        match_mode="and",
        limit=5,
    )

    assert result == {"status": "ok", "items": []}


@pytest.mark.asyncio
async def test_runtime_factory_registers_memory_tools_when_database_available(
    session_factory,
) -> None:
    registry = await AgentRuntimeFactory(
        session_factory=session_factory
    ).build_tool_registry()

    assert {"memory_read", "memory_write"}.issubset(registry.list_tool_names())
    assert registry.get("memory_read").enabled_stages == ("Run",)
    assert registry.get("memory_write").requires_market_open is False


class MemoryWriteClient:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, **_: object) -> dict[str, object]:
        self.calls += 1
        if self.calls == 1:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "memory-write-1",
                        "name": "memory_write",
                        "arguments": {
                            "operation": "create",
                            "content": "弱市缩量反弹时不要追高。",
                            "reason": "本次运行观察到追高后回撤。",
                        },
                    }
                ],
            }
        return {"content": "# Report", "tool_calls": []}


def _context(registry: ToolRegistry) -> RunExecutionContext:
    snapshot = StrategySnapshot(prompt_version="v1", risk_rules_version="v1")
    return RunExecutionContext(
        run=StrategyRun(300, TriggerSource.MANUAL, None, snapshot),
        snapshot=snapshot,
        tool_registry=registry,
        market_session_is_open=lambda: False,
    )


@pytest.mark.asyncio
async def test_run_allows_memory_write_closed_market_and_hides_tools_from_summary(
    session_factory,
) -> None:
    registry = ToolRegistry()
    registry.register(MemoryReadTool(session_factory))
    registry.register(MemoryWriteTool(session_factory))
    context = _context(registry)
    runtime = LlmRuntimeConfig(
        protocol=ModelProtocol.OPENAI_CHAT_COMPLETIONS,
        base_url="https://example.invalid",
        api_key="test",
        model="test-model",
    )
    runner = AgentRunnerFactoryAdapter(
        MemoryWriteClient(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        invocation_session_factory=session_factory,
    ).create(context, label="Run", runtime=runtime)

    result = await runner.prompt("执行任务")

    assert result.content == "# Report"
    assert any(
        activity["tool_name"] == "memory_write" and activity["status"] == "ok"
        for activity in result.tool_activity
    )
    summary_registry = _StageToolRegistry(
        registry,
        "Summary",
        run_id=300,
        invocation_session_factory=session_factory,
    )
    assert summary_registry.list_tools() == []


def _operations(tool: MemoryWriteTool) -> list[str]:
    parameters = tool.to_tool_definition()["parameters"]
    operation = parameters["properties"]["operation"]  # type: ignore[index]
    return list(operation["enum"])


def test_a_producer_is_not_offered_the_delete_branch(session_factory) -> None:
    """Pruning needs a cross-day view a single run does not have."""

    tool = MemoryWriteTool(session_factory, allowed_operations=AUTHORING_OPERATIONS)

    assert _operations(tool) == ["create", "update"]
    assert "不可删除" in tool.to_tool_definition()["description"]


def test_the_curator_keeps_every_operation(session_factory) -> None:
    tool = MemoryWriteTool(session_factory, allowed_operations=ALL_MEMORY_OPERATIONS)

    assert _operations(tool) == ["create", "update", "delete"]


@pytest.mark.asyncio
async def test_a_producer_deleting_anyway_is_refused(session_factory) -> None:
    """A model can emit a branch the schema never offered, and this one writes."""

    writer = MemoryWriteTool(session_factory)
    created = await writer.run_for_call(
        run_id=1,
        tool_call_id="call-1",
        operation="create",
        content="顺势加仓优于逆势抄底",
        reason="多次验证",
    )
    memory_id = created["item"]["id"]  # type: ignore[index]

    producer = MemoryWriteTool(session_factory, allowed_operations=AUTHORING_OPERATIONS)
    with pytest.raises(ValueError, match="not permitted"):
        await producer.run_for_call(
            run_id=2,
            tool_call_id="call-2",
            operation="delete",
            memory_id=memory_id,
            expected_version=1,
        )

    # The memory must still be there for the nightly curator to judge.
    reader = MemoryReadTool(session_factory)
    found = await reader.run_for_call(
        run_id=3, tool_call_id="call-3", keywords="加仓"
    )
    assert found["items"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_the_curator_may_still_delete(session_factory) -> None:
    writer = MemoryWriteTool(session_factory)
    created = await writer.run_for_call(
        run_id=1,
        tool_call_id="call-1",
        operation="create",
        content="重复的经验",
        reason="待清理",
    )

    curator = MemoryWriteTool(session_factory, allowed_operations=ALL_MEMORY_OPERATIONS)
    result = await curator.run_for_call(
        run_id=20260907401,
        tool_call_id="call-2",
        operation="delete",
        memory_id=created["item"]["id"],  # type: ignore[index]
        expected_version=created["item"]["version"],  # type: ignore[index]
    )

    assert result["status"] == "ok"  # type: ignore[index]


def test_a_tool_with_no_operations_is_rejected(session_factory) -> None:
    with pytest.raises(ValueError, match="at least one"):
        MemoryWriteTool(session_factory, allowed_operations=frozenset())


@pytest.mark.asyncio
async def test_a_merge_records_the_memories_it_replaced(session_factory) -> None:
    tool = MemoryWriteTool(session_factory)

    first = await tool.run_for_call(
        run_id=1,
        tool_call_id="call-1",
        operation="create",
        content="缩量反弹不追高。",
        reason="盘中观察。",
    )
    merged = await tool.run_for_call(
        run_id=20260908401,
        tool_call_id="call-2",
        operation="create",
        content="不追高：缩量反弹与利好兑现日都等确认。",
        reason="合并重复观察。",
        replaces=[first["item"]["id"]],  # type: ignore[index]
    )

    assert merged["item"]["replaces"] == [first["item"]["id"]]  # type: ignore[index]


@pytest.mark.asyncio
async def test_unusable_ids_are_dropped_rather_than_failing_the_write(
    session_factory,
) -> None:
    """The lineage is a note about what happened; one bad id must not cost the
    memory itself."""

    tool = MemoryWriteTool(session_factory)

    created = await tool.run_for_call(
        run_id=20260908401,
        tool_call_id="call-1",
        operation="create",
        content="不追高。",
        reason="合并。",
        replaces=[7, "八", -1, 0, True, 7],
    )

    assert created["item"]["replaces"] == [7]  # type: ignore[index]
