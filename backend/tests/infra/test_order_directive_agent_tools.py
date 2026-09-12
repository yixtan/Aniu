"""Tests for the Run-stage tool that hands resting orders forward."""

from __future__ import annotations

import pytest

from backend.business.order_directives import DirectiveAction
from backend.infra.integrations.agent_runtime import AgentRuntimeFactory
from backend.infra.integrations.order_directive_agent_tools import DeclareOrderPlanTool
from backend.infra.repositories import OrderDirectiveRepository

RUN_ID = 20260912101


def _entry(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "order_id": "262534700000036039",
        "symbol": "601869",
        "stock_name": "长飞光纤",
        "action": "cancel",
        "note": "筹码分散、追高赔率差，放弃，不追",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_the_plan_tool_is_offered_to_the_run_stage_only(session_factory) -> None:
    registry = await AgentRuntimeFactory(
        session_factory=session_factory
    ).build_tool_registry()

    tool = registry.get("declare_order_plan")
    assert tool is not None
    assert tool.enabled_stages == ("Run",)
    # The Summary stage renders HTML and the Dream stage curates memories;
    # neither has orders to speak for.
    assert "Summary" not in tool.enabled_stages
    assert "Dream" not in tool.enabled_stages


@pytest.mark.asyncio
async def test_declaring_a_plan_records_it_against_the_run(session_factory) -> None:
    tool = DeclareOrderPlanTool(session_factory)

    result = await tool.run_for_call(
        run_id=RUN_ID,
        tool_call_id="call-1",
        directives=[_entry()],
    )

    assert result == {"status": "ok", "accepted": 1, "downgraded": 0, "unfilable": []}
    async with session_factory() as session:
        stored = await OrderDirectiveRepository(session).list_current()
    assert [item.action for item in stored] == [DirectiveAction.CANCEL]
    assert stored[0].issued_by_run_id == RUN_ID


@pytest.mark.asyncio
async def test_a_second_call_replaces_the_first(session_factory) -> None:
    """The list is what stands right now, so two calls are not two plans."""

    tool = DeclareOrderPlanTool(session_factory)
    await tool.run_for_call(
        run_id=RUN_ID, tool_call_id="call-1", directives=[_entry(order_id="A")]
    )
    await tool.run_for_call(
        run_id=RUN_ID, tool_call_id="call-2", directives=[_entry(order_id="B")]
    )

    async with session_factory() as session:
        stored = await OrderDirectiveRepository(session).list_current()
    assert [item.order_id for item in stored] == ["B"]


@pytest.mark.asyncio
async def test_unusable_entries_are_reported_back_to_the_model(session_factory) -> None:
    tool = DeclareOrderPlanTool(session_factory)

    result = await tool.run_for_call(
        run_id=RUN_ID,
        tool_call_id="call-1",
        directives=[
            _entry(order_id="A"),
            _entry(order_id="B", action="reprice"),
            {"action": "cancel", "note": "没有 order_id"},
        ],
    )

    assert result == {
        "status": "ok",
        "accepted": 1,
        "downgraded": 1,
        "unfilable": ["directive has no order_id"],
    }


@pytest.mark.asyncio
async def test_the_plan_tool_refuses_an_untrusted_call(session_factory) -> None:
    """`run` carries no run id, so it could not attribute the authority."""

    with pytest.raises(RuntimeError):
        await DeclareOrderPlanTool(session_factory).run(directives=[_entry()])


@pytest.mark.asyncio
async def test_a_missing_directives_argument_is_refused(session_factory) -> None:
    """Omitting the list must not read as "clear the plan"."""

    with pytest.raises(ValueError):
        await DeclareOrderPlanTool(session_factory).run_for_call(
            run_id=RUN_ID, tool_call_id="call-1"
        )
