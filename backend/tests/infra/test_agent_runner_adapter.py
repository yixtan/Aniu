"""Integration tests for Agent stage policy with direct MX tools."""

from __future__ import annotations

from typing import cast

import pytest

from backend.agent.errors import AgentErrorCode, AgentIntegrationError
from backend.agent.kernel.context_budget import ContextBudgetExceededError
from backend.agent.kernel.runtime_config import LlmRuntimeConfig
from backend.agent.tools import ToolRegistry
from backend.business.runs import StrategyRun, StrategySnapshot
from backend.business.runs.execution import RunExecutionContext
from backend.business.shared import IntegrationErrorCode, ServiceIntegrationError
from backend.business.shared.enums import TriggerSource
from backend.infra.integrations.agent_runner import (
    AgentRunnerAdapter,
    AgentRunnerFactoryAdapter,
)
from backend.infra.integrations.agent_runtime import AgentRuntimeFactory
from backend.infra.integrations.mx_agent_tools import CancelTool, TradeTool
from backend.llm import ModelProtocol


class TradeRequestClient:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        self.calls += 1
        if self.calls == 1:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "trade-1",
                        "name": "trade",
                        "arguments": {"instruction": "买入 600519 1700 100"},
                    }
                ],
            }
        return {"content": "交易被阻止。", "tool_calls": []}


@pytest.mark.asyncio
async def test_adapter_preserves_context_error_codes() -> None:
    class OverflowHarness:
        async def prepare_prompt(self, *args: object, **kwargs: object) -> str:
            del args, kwargs
            raise ContextBudgetExceededError("context cannot fit")

        async def prompt(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            raise AgentIntegrationError(
                "provider context overflow",
                status_code=409,
                error_code=AgentErrorCode.CONTEXT_OVERFLOW,
            )

    adapter = AgentRunnerAdapter(harness=OverflowHarness(), label="Run")  # type: ignore[arg-type]

    with pytest.raises(ServiceIntegrationError) as prepared:
        await adapter.prepare_prompt("payload")
    assert prepared.value.error_code is IntegrationErrorCode.CONTEXT_OVERFLOW

    with pytest.raises(ServiceIntegrationError) as prompted:
        await adapter.prompt("payload")
    assert prompted.value.error_code is IntegrationErrorCode.CONTEXT_OVERFLOW


class SnapshotRuntimeFactory:
    def __init__(self) -> None:
        self.registry_requested = False

    async def build_tool_registry(self) -> ToolRegistry:
        self.registry_requested = True
        return ToolRegistry()

    async def build_stage_runtimes(
        self,
        snapshot: StrategySnapshot,
    ) -> dict[str, LlmRuntimeConfig]:
        del snapshot
        return {}


@pytest.mark.asyncio
async def test_prepare_builds_fixed_tool_registry_without_snapshot_preferences() -> (
    None
):
    runtime_factory = SnapshotRuntimeFactory()
    snapshot = StrategySnapshot(prompt_version="v1", risk_rules_version="v1")
    adapter = AgentRunnerFactoryAdapter(
        TradeRequestClient(),  # type: ignore[arg-type]
        cast(AgentRuntimeFactory, runtime_factory),
    )

    await adapter.prepare(snapshot)

    assert runtime_factory.registry_requested is True


@pytest.mark.asyncio
async def test_closed_market_blocks_typed_trade_tool() -> None:
    registry = ToolRegistry()
    registry.register(TradeTool(object()))  # type: ignore[arg-type]
    snapshot = StrategySnapshot(prompt_version="v1", risk_rules_version="v1")
    run = StrategyRun(1, TriggerSource.MANUAL, None, snapshot)
    context = RunExecutionContext(
        run=run,
        snapshot=snapshot,
        tool_registry=registry,
        market_session_is_open=lambda: False,
    )
    runtime = LlmRuntimeConfig(
        protocol=ModelProtocol.OPENAI_CHAT_COMPLETIONS,
        base_url="https://example.invalid",
        api_key="test",
        model="test-model",
    )
    factory = AgentRunnerFactoryAdapter(
        TradeRequestClient(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )

    runner = factory.create(context, label="Run", runtime=runtime)
    result = await runner.prompt("执行交易")

    assert result.content == "交易被阻止。"
    assert result.tool_activity[0]["status"] == "blocked"
    assert "非交易时段" in str(result.tool_activity[0]["error"])


class CancelRequestClient:
    """Asks to cancel once, then reports what it was told."""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        self.calls += 1
        if self.calls == 1:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "cancel-1",
                        "name": "cancel",
                        "arguments": {"instruction": "撤单 262574700000048096 688008"},
                    }
                ],
            }
        return {"content": "撤单被阻止。", "tool_calls": []}


def _runtime() -> LlmRuntimeConfig:
    return LlmRuntimeConfig(
        protocol=ModelProtocol.OPENAI_CHAT_COMPLETIONS,
        base_url="https://example.invalid",
        api_key="test",
        model="test-model",
    )


def _context(registry: ToolRegistry, **session: object) -> RunExecutionContext:
    snapshot = StrategySnapshot(prompt_version="v1", risk_rules_version="v1")
    return RunExecutionContext(
        run=StrategyRun(1, TriggerSource.MANUAL, None, snapshot),
        snapshot=snapshot,
        tool_registry=registry,
        **session,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_the_closing_auction_blocks_a_cancel_and_says_not_to_retry() -> None:
    """2026-09-15 14:57: the exchange refused with a bare "撤单失败".

    Not knowing why, the watch tried again about twenty-five times in fifty
    seconds, tripped the provider's rate limiter — which then failed its
    account reads too — and died on the watch deadline. Three minutes before
    that, the same cancel had worked. The refusal has to carry the reason and
    say that retrying cannot help, because retrying is the failure.
    """

    registry = ToolRegistry()
    registry.register(CancelTool(object()))  # type: ignore[arg-type]
    context = _context(
        registry,
        market_session_is_open=lambda: True,
        cancellations_accepted=lambda: False,
    )
    factory = AgentRunnerFactoryAdapter(
        CancelRequestClient(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )

    result = await factory.create(context, label="Watch", runtime=_runtime()).prompt(
        "按计划撤单"
    )

    assert result.tool_activity[0]["status"] == "blocked"
    error = str(result.tool_activity[0]["error"])
    assert "收盘集合竞价" in error
    assert "重试不会成功" in error


@pytest.mark.asyncio
async def test_an_order_may_still_be_placed_during_the_closing_auction() -> None:
    """The auction takes orders; only withdrawals stop. Blocking both would
    cost a legitimate trade to fix a cancel that was never going to work."""

    registry = ToolRegistry()
    registry.register(TradeTool(object()))  # type: ignore[arg-type]
    context = _context(
        registry,
        market_session_is_open=lambda: True,
        cancellations_accepted=lambda: False,
    )
    factory = AgentRunnerFactoryAdapter(
        TradeRequestClient(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )

    result = await factory.create(context, label="Run", runtime=_runtime()).prompt(
        "执行交易"
    )

    assert result.tool_activity[0]["status"] != "blocked"


@pytest.mark.asyncio
async def test_a_context_without_the_narrower_answer_blocks_nothing_new() -> None:
    """Older callers keep the behaviour they had; the exchange still refuses."""

    registry = ToolRegistry()
    registry.register(CancelTool(object()))  # type: ignore[arg-type]
    context = _context(registry, market_session_is_open=lambda: True)
    factory = AgentRunnerFactoryAdapter(
        CancelRequestClient(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )

    result = await factory.create(context, label="Watch", runtime=_runtime()).prompt(
        "按计划撤单"
    )

    assert result.tool_activity[0]["status"] != "blocked"
