"""An analysis is shown what the last one decided, before it decides again."""

from __future__ import annotations

import pytest

from backend.business.order_directives import DirectiveAction, OrderDirective
from backend.business.runs import StrategyRun, StrategySnapshot
from backend.business.runs.execution import (
    RunExecutionContext,
    RunReport,
    SummaryDraft,
)
from backend.business.runs.orchestration import AniuOrchestrator
from backend.business.shared.enums import RunState, TriggerSource
from backend.tests.business.test_aniu_orchestrator import (
    NullAgentFactory,
    RecordingCallbacks,
    ScriptedSummaryStage,
    make_run,
)


def _directive(order_id: str) -> OrderDirective:
    return OrderDirective(
        order_id=order_id,
        symbol="688008",
        stock_name="澜起科技",
        action=DirectiveAction.HOLD,
        note="185 回踩承接；涨破 190 则回踩逻辑失效",
        issued_by_run_id=20260915107,
        cancel_if_price_above=190.0,
    )


class CapturingRunStage:
    """Records the context it was handed, which is what this is all about."""

    def __init__(self) -> None:
        self.seen: RunExecutionContext | None = None

    async def execute(self, context, runner):
        del runner
        self.seen = context
        return RunReport(content="# 报告")


class StaticPlan:
    def __init__(self, current=(), previous=()) -> None:
        self._current = current
        self._previous = previous
        self.previous_reads = 0

    async def current(self) -> tuple[OrderDirective, ...]:
        return self._current

    async def previous(self) -> tuple[OrderDirective, ...]:
        self.previous_reads += 1
        return self._previous


class BrokenPlan:
    async def current(self) -> tuple[OrderDirective, ...]:
        raise RuntimeError("database went away")

    async def previous(self) -> tuple[OrderDirective, ...]:
        raise RuntimeError("database went away")


def _orchestrator(run_stage: CapturingRunStage, order_plan) -> AniuOrchestrator:
    return AniuOrchestrator(
        state_callbacks=RecordingCallbacks(),
        agent_runner_factory=NullAgentFactory(),
        stage_runtimes={"Run": object(), "Summary": object()},
        run_stage=run_stage,  # type: ignore[arg-type]
        summary_stage=ScriptedSummaryStage([SummaryDraft(summary="<p/>")]),
        market_session_is_open=lambda _moment: True,
        order_plan=order_plan,
    )


@pytest.mark.asyncio
async def test_the_standing_plan_reaches_the_run_stage() -> None:
    run_stage = CapturingRunStage()
    plan = StaticPlan(
        current=(_directive("262574600000039111"),),
        previous=(_directive("262574700000036664"),),
    )

    await _orchestrator(run_stage, plan).execute(make_run())

    assert run_stage.seen is not None
    assert [item.order_id for item in run_stage.seen.standing_order_plan] == [
        "262574600000039111"
    ]
    assert [item.order_id for item in run_stage.seen.previous_order_plan] == [
        "262574700000036664"
    ]


@pytest.mark.asyncio
async def test_a_plan_that_cannot_be_read_costs_context_not_the_run() -> None:
    """The opposite of the watch, where an unreadable plan has to forbid acting.

    Here the plan authorizes nothing, so failing to read it leaves the run as
    blind as every run was before, which is survivable — and failing the run
    over a missing reference would not be.
    """

    run_stage = CapturingRunStage()

    result = await _orchestrator(run_stage, BrokenPlan()).execute(make_run())

    assert run_stage.seen is not None
    assert run_stage.seen.standing_order_plan == ()
    assert run_stage.seen.previous_order_plan == ()
    assert result.pending_report is not None


@pytest.mark.asyncio
async def test_an_orchestrator_without_a_plan_port_still_runs() -> None:
    run_stage = CapturingRunStage()

    await _orchestrator(run_stage, None).execute(make_run())

    assert run_stage.seen is not None
    assert run_stage.seen.standing_order_plan == ()


@pytest.mark.asyncio
async def test_a_watch_is_never_shown_the_superseded_plan() -> None:
    """A watch acts on one list. Two would only invite it to weigh them."""

    class StaticWatchStage:
        def __init__(self) -> None:
            self.seen_context: RunExecutionContext | None = None

        async def execute(self, context, runner, *, directives=()):
            del runner, directives
            self.seen_context = context
            return RunReport(content="核对完毕")

    watch_stage = StaticWatchStage()
    plan = StaticPlan(
        current=(_directive("262574600000039111"),),
        previous=(_directive("262574700000036664"),),
    )
    orchestrator = AniuOrchestrator(
        state_callbacks=RecordingCallbacks(),
        agent_runner_factory=NullAgentFactory(),
        stage_runtimes={"Watch": object()},
        run_stage=CapturingRunStage(),  # type: ignore[arg-type]
        summary_stage=ScriptedSummaryStage([SummaryDraft(summary="<p/>")]),
        watch_stage=watch_stage,  # type: ignore[arg-type]
        market_session_is_open=lambda _moment: True,
        order_plan=plan,
    )
    watch = StrategyRun(
        run_id=20260915301,
        trigger_source=TriggerSource.SCHEDULED,
        schedule_id=1,
        snapshot=StrategySnapshot(prompt_version="v3", risk_rules_version="risk-v1"),
        current_state=RunState.WATCH,
    )

    await orchestrator.execute(watch)

    assert watch_stage.seen_context is not None
    assert watch_stage.seen_context.standing_order_plan == ()
    assert watch_stage.seen_context.previous_order_plan == ()
    assert plan.previous_reads == 0
