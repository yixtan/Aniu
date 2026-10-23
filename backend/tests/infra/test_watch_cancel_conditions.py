"""Watch cannot reason past a HOLD or an unmet condition."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from backend.agent.kernel.runtime_config import LlmRuntimeConfig
from backend.agent.tools import ToolRegistry
from backend.business.order_directives import (
    DirectiveAction,
    OrderDirective,
    RepricePlan,
)
from backend.business.runs import StrategyRun, StrategySnapshot
from backend.business.runs.execution import RunExecutionContext
from backend.business.shared.enums import TriggerSource
from backend.infra.integrations.agent_runner import AgentRunnerFactoryAdapter
from backend.infra.integrations.mx_agent_tools import CancelTool
from backend.llm import ModelProtocol
from backend.tests.infra.test_exposure_execution_guard import clients, order

NOW = datetime(2026, 10, 23, 2, tzinfo=UTC)


@pytest.fixture(autouse=True)
def watch_clock(monkeypatch):
    monkeypatch.setattr(
        "backend.infra.integrations.watch_cancel_guard.utc_now", lambda: NOW
    )


def directive(**kwargs):
    return OrderDirective(
        **{
            **dict(
                order_id="test",
                symbol="600000",
                stock_name="合成测试",
                action=DirectiveAction.HOLD,
                note="合成计划",
                issued_by_run_id=1,
            ),
            **kwargs,
        }
    )


def setup_tool(plan=None):
    p, t = clients()
    p.get_orders.return_value = [order()]
    market = AsyncMock()
    market.execute_fresh.return_value = {
        "data": {
            "quotes": [
                {"symbol": "600000.SH", "price": 10, "market_time": NOW.isoformat()}
            ]
        }
    }
    return CancelTool(t, p, market_data=market), t, market


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "condition",
    [
        {},
        {"cancel_if_price_above": 11},
        {"cancel_if_price_below": 9},
        {"cancel_if_unfilled_after": time(10, 1)},
    ],
)
async def test_hold_or_unmet_conditions_never_send_cancellation(condition):
    tool, t, _ = setup_tool()
    with pytest.raises(ValueError, match="尚未触发|继续持有"):
        await tool.run_for_watch(
            instruction="撤单 test 600000",
            cancellations_accepted=lambda: True,
            directives=(directive(**condition),),
        )
    t.cancel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "condition",
    [
        {"cancel_if_price_above": 9},
        {"cancel_if_price_below": 11},
        {"cancel_if_unfilled_after": time(10)},
    ],
)
async def test_triggered_condition_sends_cancellation(condition):
    tool, t, _ = setup_tool()
    await tool.run_for_watch(
        instruction="撤单 test 600000",
        cancellations_accepted=lambda: True,
        directives=(directive(**condition),),
    )
    t.cancel.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_cancel_needs_no_quote():
    tool, t, market = setup_tool()
    await tool.run_for_watch(
        instruction="撤单 test 600000",
        cancellations_accepted=lambda: True,
        directives=(replace(directive(), action=DirectiveAction.CANCEL),),
    )
    t.cancel.assert_awaited_once()
    market.execute_fresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), 0, -1])
async def test_bad_quote_cannot_authorize_write(value):
    tool, t, market = setup_tool()
    market.execute_fresh.return_value["data"]["quotes"][0]["price"] = value
    with pytest.raises(ValueError, match="价格无效"):
        await tool.run_for_watch(
            instruction="撤单 test 600000",
            cancellations_accepted=lambda: True,
            directives=(directive(cancel_if_price_above=9),),
        )
    t.cancel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stamp",
    [
        None,
        (NOW - timedelta(seconds=61)).isoformat(),
        (NOW + timedelta(seconds=6)).isoformat(),
    ],
)
async def test_bad_or_stale_market_time_cannot_authorize_write(stamp):
    tool, t, market = setup_tool()
    market.execute_fresh.return_value["data"]["quotes"][0]["market_time"] = stamp
    with pytest.raises(ValueError, match="时间"):
        await tool.run_for_watch(
            instruction="撤单 test 600000",
            cancellations_accepted=lambda: True,
            directives=(directive(cancel_if_price_above=9),),
        )
    t.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_network_error_does_not_become_permission():
    tool, t, market = setup_tool()
    market.execute_fresh.side_effect = OSError("isolated offline")
    with pytest.raises(OSError):
        await tool.run_for_watch(
            instruction="撤单 test 600000",
            cancellations_accepted=lambda: True,
            directives=(directive(cancel_if_price_above=9),),
        )
    t.cancel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("plans", [None, (), (directive(), directive())])
async def test_missing_or_duplicate_plan_blocks(plans):
    tool, t, _ = setup_tool()
    with pytest.raises(ValueError):
        await tool.run_for_watch(
            instruction="撤单 test 600000",
            cancellations_accepted=lambda: True,
            directives=plans,
        )
    t.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_reprice_does_not_cancel_without_ability_to_replace():
    tool, t, _ = setup_tool()
    plan = replace(
        directive(),
        action=DirectiveAction.REPRICE,
        reprice=RepricePlan(new_price=11, when_price_above=9),
    )
    with pytest.raises(ValueError, match="改价"):
        await tool.run_for_watch(
            instruction="撤单 test 600000",
            cancellations_accepted=lambda: True,
            directives=(plan,),
        )
    t.cancel.assert_not_awaited()


class RequestCancel:
    def __init__(self):
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "cancel-1",
                        "name": "cancel",
                        "arguments": {"instruction": "撤单 test 600000"},
                    }
                ],
            }
        return {"content": "已按工具结果记录", "tool_calls": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [False, True])
@pytest.mark.parametrize("label", ["Watch", "Watch:repeat"])
async def test_real_stage_adapter_checks_trusted_plan_not_just_id(allowed, label):
    tool, t, _ = setup_tool()
    registry = ToolRegistry()
    registry.register(tool)
    snap = StrategySnapshot(prompt_version="test", risk_rules_version="test")
    ctx = RunExecutionContext(
        run=StrategyRun(2, TriggerSource.MANUAL, None, snap),
        snapshot=snap,
        tool_registry=registry,
        market_session_is_open=lambda: True,
        cancellations_accepted=lambda: True,
    )
    plan = directive(cancel_if_price_above=9 if allowed else 11)
    ctx.watch_order_plan = (plan, directive(order_id="other", symbol="600001"))
    ctx.authorized_order_ids = frozenset(["test", "other"])
    runtime = LlmRuntimeConfig(
        protocol=ModelProtocol.OPENAI_CHAT_COMPLETIONS,
        base_url="https://isolated.invalid",
        api_key="test",
        model="test",
    )
    result = (
        await AgentRunnerFactoryAdapter(RequestCancel(), object())
        .create(ctx, label=label, runtime=runtime)
        .prompt("隔离盯盘")
    )
    if allowed:
        t.cancel.assert_awaited_once()
    else:
        t.cancel.assert_not_awaited()
    assert result.content


@pytest.mark.asyncio
async def test_session_can_close_while_order_query_is_in_flight():
    tool, t, _ = setup_tool()
    accepted = Mock(side_effect=[True, False])
    with pytest.raises(ValueError, match="时段已结束"):
        await tool.run_for_watch(
            instruction="撤单 test 600000",
            directives=(replace(directive(), action=DirectiveAction.CANCEL),),
            cancellations_accepted=accepted,
        )
    t.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_session_callback_is_not_permission():
    tool, t, _ = setup_tool()
    with pytest.raises(ValueError, match="时段无法确认"):
        await tool.run_for_watch(
            instruction="撤单 test 600000",
            directives=(replace(directive(), action=DirectiveAction.CANCEL),),
        )
    t.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_time_trigger_can_cancel_even_if_quote_source_offline():
    tool, t, market = setup_tool()
    market.execute_fresh.side_effect = OSError("isolated offline")
    await tool.run_for_watch(
        instruction="撤单 test 600000",
        directives=(
            directive(cancel_if_price_above=11, cancel_if_unfilled_after=time(10)),
        ),
        cancellations_accepted=lambda: True,
    )
    t.cancel.assert_awaited_once()
    market.execute_fresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_stock_identity_does_not_authorize_cancellation():
    tool, t, _ = setup_tool()
    with pytest.raises(ValueError, match="不一致"):
        await tool.run_for_watch(
            instruction="撤单 test 600001",
            directives=(replace(directive(), action=DirectiveAction.CANCEL),),
            cancellations_accepted=lambda: True,
        )
    t.cancel.assert_not_awaited()
