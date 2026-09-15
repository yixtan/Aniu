"""Tests for the single continuous Run agent stage."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.business.order_directives import DirectiveAction, OrderDirective
from backend.business.runs import StrategyRun, StrategySnapshot
from backend.business.runs.agent_runner import AgentStageResult
from backend.business.runs.execution import RunExecutionContext
from backend.business.runs.stages.run_stage import RunStage
from backend.business.shared.enums import TriggerSource


class RecordingRunner:
    def __init__(self, result: AgentStageResult) -> None:
        self.result = result
        self.prompts: list[str] = []

    async def prepare_prompt(self, message: str, **kwargs: object) -> str:
        del kwargs
        return message

    async def prompt(
        self, message: str, *, abort_signal: object | None = None
    ) -> AgentStageResult:
        del abort_signal
        self.prompts.append(message)
        return self.result


def _context(*, market_open: bool) -> RunExecutionContext:
    snapshot = StrategySnapshot(
        prompt_version="v3",
        risk_rules_version="risk-v1",
    )
    run = StrategyRun(
        run_id=1,
        trigger_source=TriggerSource.MANUAL,
        schedule_id=None,
        snapshot=snapshot,
    )
    context = RunExecutionContext(run=run, snapshot=snapshot)
    context.llm_runtime = SimpleNamespace()
    context.market_session_is_open = lambda: market_open
    return context


@pytest.mark.asyncio
async def test_run_stage_uses_one_prompt_and_preserves_raw_execution_evidence() -> None:
    activity = (
        {
            "tool_call_id": "quote-1",
            "tool_name": "query_quote",
            "status": "ok",
            "content": {"price": 10},
        },
    )
    transcript = (
        {"role": "assistant", "reasoning": "inspect market"},
        {"role": "assistant", "content": "# Final report"},
    )
    runner = RecordingRunner(
        AgentStageResult(
            content="  # Final report\n\nNo trade.  ",
            tool_activity=activity,
            transcript=transcript,
        )
    )

    result = await RunStage().execute(_context(market_open=False), runner)

    assert result.content == "# Final report\n\nNo trade."
    assert result.tool_activity == activity
    assert result.transcript == transcript
    assert len(runner.prompts) == 1
    assert '"market_session_open":false' in runner.prompts[0]
    assert "最终只输出 Markdown 报告正文" in runner.prompts[0]


@pytest.mark.asyncio
async def test_run_stage_rejects_empty_report() -> None:
    runner = RecordingRunner(AgentStageResult(content="  \n"))

    with pytest.raises(ValueError, match="run report must not be empty"):
        await RunStage().execute(_context(market_open=True), runner)


@pytest.mark.asyncio
async def test_run_stage_requires_runtime_before_emitting_or_prompting() -> None:
    context = _context(market_open=True)
    context.llm_runtime = None
    runner = RecordingRunner(AgentStageResult(content="# unused"))

    with pytest.raises(Exception, match="configured llm runtime"):
        await RunStage().execute(context, runner)

    assert runner.prompts == []


def _with_watchlist(
    followed: tuple[tuple[str, str], ...],
    *,
    watchlist_prompt: str = "",
) -> RunExecutionContext:
    context = _context(market_open=True)
    context.followed_companies = followed
    run_stage = context.snapshot.stage_settings["Run"]
    context.snapshot.stage_settings["Run"] = replace(
        run_stage, watchlist_prompt=watchlist_prompt
    )
    return context


@pytest.mark.asyncio
async def test_a_watchlist_reaches_the_model_with_its_instruction() -> None:
    context = _with_watchlist(
        (("600519.SH", "贵州茅台"), ("300750.SZ", "宁德时代")),
        watchlist_prompt="先快速筛查关注清单，再决定是否深入。",
    )
    runner = RecordingRunner(AgentStageResult(content="报告"))

    await RunStage().execute(context, runner)

    prompt = runner.prompts[0]
    assert "600519.SH" in prompt
    assert "贵州茅台" in prompt
    assert "先快速筛查关注清单" in prompt


@pytest.mark.asyncio
async def test_an_empty_watchlist_sends_neither_list_nor_instruction() -> None:
    """Asking a model to consider an empty list only invites it to say so."""

    context = _with_watchlist((), watchlist_prompt="先快速筛查关注清单。")
    runner = RecordingRunner(AgentStageResult(content="报告"))

    await RunStage().execute(context, runner)

    prompt = runner.prompts[0]
    assert "watchlist" not in prompt
    assert "先快速筛查关注清单" not in prompt


@pytest.mark.asyncio
async def test_a_watchlist_without_an_instruction_still_reaches_the_model() -> None:
    """The list is the fact; the instruction is optional wording about it."""

    context = _with_watchlist((("600519.SH", "贵州茅台"),))
    runner = RecordingRunner(AgentStageResult(content="报告"))

    await RunStage().execute(context, runner)

    assert "600519.SH" in runner.prompts[0]


def _directive(
    *,
    order_id: str = "262574600000039111",
    note: str = "185 回踩承接；涨破 190 则回踩逻辑失效",
    cancel_if_price_above: float | None = 190.0,
    issued_by_run_id: int = 20260915107,
) -> OrderDirective:
    return OrderDirective(
        order_id=order_id,
        symbol="688008",
        stock_name="澜起科技",
        action=DirectiveAction.HOLD,
        note=note,
        issued_by_run_id=issued_by_run_id,
        cancel_if_price_above=cancel_if_price_above,
    )


@pytest.mark.asyncio
async def test_an_analysis_is_shown_the_plan_it_is_about_to_replace() -> None:
    """The gap that let 2026-09-15 happen.

    The analysis at 13:20 saw an account with no pending orders and reasoned
    from scratch, so it never learned that a watch had cancelled its
    predecessor's order twenty minutes earlier under a condition that
    predecessor had itself written.
    """

    context = _context(market_open=True)
    context.standing_order_plan = (_directive(),)
    runner = RecordingRunner(AgentStageResult(content="报告"))

    await RunStage().execute(context, runner)

    prompt = runner.prompts[0]
    assert "standing_order_plan" in prompt
    assert "262574600000039111" in prompt
    assert "涨破 190" in prompt
    # Whose decision it was, because this run is deciding whether to repeat it.
    assert "20260915107" in prompt


@pytest.mark.asyncio
async def test_the_generation_before_it_comes_along_when_there_is_one() -> None:
    context = _context(market_open=True)
    context.standing_order_plan = (_directive(),)
    context.previous_order_plan = (
        _directive(order_id="262574700000036664", note="更早的一次承接单"),
    )
    runner = RecordingRunner(AgentStageResult(content="报告"))

    await RunStage().execute(context, runner)

    prompt = runner.prompts[0]
    assert "previous_order_plan" in prompt
    assert "262574700000036664" in prompt


@pytest.mark.asyncio
async def test_no_plan_sends_no_plan_section() -> None:
    """Same reason as the empty watchlist: nothing to review, nothing to say."""

    context = _context(market_open=True)
    runner = RecordingRunner(AgentStageResult(content="报告"))

    await RunStage().execute(context, runner)

    prompt = runner.prompts[0]
    assert "standing_order_plan" not in prompt
    assert "previous_order_plan" not in prompt
