"""A watch run takes its own path through the orchestrator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from backend.business.order_directives import DirectiveAction, OrderDirective
from backend.business.runs import StrategyRun, StrategySnapshot
from backend.business.runs.execution import RunReport
from backend.business.runs.orchestration import AniuOrchestrator
from backend.business.shared import RunAbortError
from backend.business.shared.enums import RunState, RunStatus, TriggerSource
from backend.tests.business.test_aniu_orchestrator import (
    NullAgentFactory,
    RecordingCallbacks,
)


def make_watch_run() -> StrategyRun:
    return StrategyRun(
        run_id=20260913301,
        trigger_source=TriggerSource.SCHEDULED,
        schedule_id=1,
        snapshot=StrategySnapshot(prompt_version="v3", risk_rules_version="risk-v1"),
        current_state=RunState.WATCH,
    )


def _directive(order_id: str) -> OrderDirective:
    return OrderDirective(
        order_id=order_id,
        symbol="601869",
        stock_name="长飞光纤",
        action=DirectiveAction.HOLD,
        note="留着",
        issued_by_run_id=20260913101,
    )


@dataclass
class StaticWatchStage:
    report: RunReport = field(
        default_factory=lambda: RunReport(content="逐笔核对完毕。")
    )
    calls: int = 0
    seen_directives: tuple[OrderDirective, ...] = ()
    seen_authorized: frozenset[str] | None = None

    async def execute(self, context, runner, *, directives=()) -> RunReport:
        del runner
        self.calls += 1
        self.seen_directives = tuple(directives)
        self.seen_authorized = context.authorized_order_ids
        return self.report


class ExplodingStage:
    async def execute(self, *args, **kwargs):
        raise AssertionError("an analysis stage must not run inside a watch")


@dataclass
class StaticPlan:
    directives: tuple[OrderDirective, ...] = ()

    async def current(self) -> tuple[OrderDirective, ...]:
        return self.directives


class BrokenPlan:
    async def current(self) -> tuple[OrderDirective, ...]:
        raise RuntimeError("database went away")


def make_orchestrator(
    callbacks: RecordingCallbacks, *, watch_stage: StaticWatchStage, order_plan
) -> AniuOrchestrator:
    return AniuOrchestrator(
        state_callbacks=callbacks,
        agent_runner_factory=NullAgentFactory(),
        stage_runtimes={"Watch": object()},
        run_stage=ExplodingStage(),  # type: ignore[arg-type]
        summary_stage=ExplodingStage(),  # type: ignore[arg-type]
        watch_stage=watch_stage,  # type: ignore[arg-type]
        market_session_is_open=lambda _moment: True,
        order_plan=order_plan,
    )


@pytest.mark.asyncio
async def test_a_watch_is_one_stage_and_never_touches_summary() -> None:
    callbacks = RecordingCallbacks()
    stage = StaticWatchStage()
    run = make_watch_run()

    result = await make_orchestrator(
        callbacks, watch_stage=stage, order_plan=StaticPlan()
    ).execute(run)

    assert stage.calls == 1
    assert result.final_state is RunState.COMPLETED
    assert run.status is RunStatus.COMPLETED
    assert callbacks.entered == ["20260913301:Watch", "20260913301:Completed"]
    assert callbacks.completed == ["20260913301:Watch"]
    assert run.summary == "逐笔核对完毕。"


@pytest.mark.asyncio
async def test_the_plan_the_model_sees_is_the_plan_the_authorizer_enforces() -> None:
    """One list, read once, so the two cannot drift apart."""

    stage = StaticWatchStage()
    plan = StaticPlan((_directive("111"), _directive("222")))

    await make_orchestrator(
        RecordingCallbacks(), watch_stage=stage, order_plan=plan
    ).execute(make_watch_run())

    assert [item.order_id for item in stage.seen_directives] == ["111", "222"]
    assert stage.seen_authorized == frozenset({"111", "222"})


@pytest.mark.asyncio
async def test_an_empty_plan_authorizes_nothing_rather_than_everything() -> None:
    """An empty set, not None: a plan was read and it named no order."""

    stage = StaticWatchStage()

    await make_orchestrator(
        RecordingCallbacks(), watch_stage=stage, order_plan=StaticPlan()
    ).execute(make_watch_run())

    assert stage.seen_authorized == frozenset()


@pytest.mark.asyncio
async def test_a_plan_that_cannot_be_read_degrades_to_the_safe_side() -> None:
    """Failing to read the plan must not become licence to act."""

    stage = StaticWatchStage()

    await make_orchestrator(
        RecordingCallbacks(), watch_stage=stage, order_plan=BrokenPlan()
    ).execute(make_watch_run())

    assert stage.calls == 1
    assert stage.seen_directives == ()
    assert stage.seen_authorized == frozenset()


class StallingWatchStage:
    """A stage that never answers, the way a dribbling provider never does."""

    def __init__(self) -> None:
        self.aborted_reason: str | None = None

    async def execute(self, context, runner, *, directives=()) -> RunReport:
        del runner, directives
        signal = context.abort_signal
        await signal.wait()
        self.aborted_reason = signal.reason
        signal.throw_if_aborted()
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_a_watch_that_outstays_its_deadline_gives_up_its_slot() -> None:
    """The failure this exists for: on 2026-09-14 two watches ran five and a
    half minutes each, back to back, and swallowed the 14:40 analysis."""

    stage = StallingWatchStage()
    run = make_watch_run()
    orchestrator = AniuOrchestrator(
        state_callbacks=RecordingCallbacks(),
        agent_runner_factory=NullAgentFactory(),
        stage_runtimes={"Watch": object()},
        run_stage=ExplodingStage(),  # type: ignore[arg-type]
        summary_stage=ExplodingStage(),  # type: ignore[arg-type]
        watch_stage=stage,  # type: ignore[arg-type]
        market_session_is_open=lambda _moment: True,
        order_plan=StaticPlan(),
        watch_deadline_seconds=0.01,
    )

    with pytest.raises(RunAbortError) as raised:
        await orchestrator.execute(run)

    # The reason travels with the error, so the trace can say which kind of
    # stop this was — the system giving up, not somebody pressing a button.
    assert stage.aborted_reason is not None
    assert "让位" in stage.aborted_reason
    assert "让位" in str(raised.value)


@pytest.mark.asyncio
async def test_a_watch_that_answers_in_time_is_left_alone() -> None:
    callbacks = RecordingCallbacks()
    stage = StaticWatchStage()
    run = make_watch_run()
    orchestrator = AniuOrchestrator(
        state_callbacks=callbacks,
        agent_runner_factory=NullAgentFactory(),
        stage_runtimes={"Watch": object()},
        run_stage=ExplodingStage(),  # type: ignore[arg-type]
        summary_stage=ExplodingStage(),  # type: ignore[arg-type]
        watch_stage=stage,  # type: ignore[arg-type]
        market_session_is_open=lambda _moment: True,
        order_plan=StaticPlan(),
        watch_deadline_seconds=30.0,
    )

    result = await orchestrator.execute(run)

    assert result.status is RunStatus.COMPLETED
    assert run.summary == "逐笔核对完毕。"
    # The timer must not outlive the run it was guarding.
    assert not [
        task
        for task in asyncio.all_tasks()
        if task.get_name().startswith("watch-deadline:")
    ]


def test_the_deadline_cannot_be_long_enough_to_cost_an_analysis_its_slot() -> None:
    """Re-derives the ceiling instead of trusting the number.

    A watch may already be running when an analysis fires, and the analysis
    waits only so long. So the deadline has to fit inside that wait plus the
    head start the watch had — the closest the two grids ever come.
    """

    from backend.bootstrap.schedule_handlers import ANALYSIS_WAITS_FOR_WATCH_SECONDS
    from backend.business.runs.orchestration import WATCH_DEADLINE_SECONDS
    from backend.business.schedules.models import (
        ANALYSIS_TIMETABLE,
        ORDER_WATCH_TASK_TYPE,
        derive_intraday_schedule_times,
    )
    from backend.infra.scheduler.job_runner import WATCH_FIRES_SECONDS_LATE

    def minutes(clock: str) -> int:
        hours, mins = clock.split(":")
        return int(hours) * 60 + int(mins)

    watch_starts = [
        minutes(time) * 60 + WATCH_FIRES_SECONDS_LATE
        for time in derive_intraday_schedule_times(3, ORDER_WATCH_TASK_TYPE)
    ]
    head_starts = [
        minutes(analysis) * 60 - start
        for times in ANALYSIS_TIMETABLE.values()
        for analysis in times
        for start in watch_starts
        if 0 < minutes(analysis) * 60 - start
    ]

    # The worst case is the watch that started most recently before an analysis.
    closest_head_start = min(head_starts)
    assert (
        WATCH_DEADLINE_SECONDS
        <= closest_head_start + ANALYSIS_WAITS_FOR_WATCH_SECONDS
    )
