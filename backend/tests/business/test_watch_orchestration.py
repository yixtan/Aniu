"""A watch run takes its own path through the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from backend.business.order_directives import DirectiveAction, OrderDirective
from backend.business.runs import StrategyRun, StrategySnapshot
from backend.business.runs.execution import RunReport
from backend.business.runs.orchestration import AniuOrchestrator
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
