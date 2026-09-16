"""Tests for worker execution fencing in the run executor."""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.business.notifications import (
    NotificationEvent,
    NotificationEventKind,
)
from backend.business.runs import StrategyRun, StrategySnapshot, TraceStep
from backend.business.runs.abort_registry import ActiveRunAbortRegistry
from backend.business.runs.execution import RunReport
from backend.business.runs.executor import RunExecutor
from backend.business.runs.orchestration import RunResult
from backend.business.shared import RunAbortError
from backend.business.shared.enums import RunState, RunStatus, TriggerSource


class InMemoryRunRepository:
    def __init__(self, run: StrategyRun) -> None:
        self.run = run

    async def get_by_id(self, run_id: int) -> StrategyRun | None:
        return self.run if run_id == self.run.run_id else None

    async def save(self, run: StrategyRun) -> StrategyRun:
        self.run = run
        return run


class FailingAgentRunnerFactory:
    def __init__(self) -> None:
        self.prepared = False

    async def prepare(self, _snapshot: StrategySnapshot) -> object:
        self.prepared = True
        raise AssertionError("execution guard must run before agent preparation")


@pytest.mark.asyncio
async def test_execution_guard_runs_after_abort_signal_activation() -> None:
    run = StrategyRun(
        run_id=20260823101,
        trigger_source=TriggerSource.MANUAL,
        schedule_id=None,
        snapshot=StrategySnapshot(
            prompt_version="v1",
            risk_rules_version="risk-v1",
        ),
    )
    repository = InMemoryRunRepository(run)
    registry = ActiveRunAbortRegistry()
    agent_factory = FailingAgentRunnerFactory()
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=agent_factory,  # type: ignore[arg-type]
        abort_registry=registry,
    )

    async def reject_expired_claim() -> None:
        assert registry.active_run_ids == frozenset({run.run_id})
        raise RunAbortError(run.run_id)

    executor.set_execution_guard(reject_expired_claim)

    with pytest.raises(RunAbortError):
        await executor.execute(run.run_id)

    assert agent_factory.prepared is False
    assert repository.run.status is RunStatus.ABORTED
    assert registry.active_run_ids == frozenset()


class RecordingNotifier:
    def __init__(self) -> None:
        self.published: list[NotificationEvent] = []

    async def publish(self, event: NotificationEvent) -> None:
        self.published.append(event)


class ExplodingAgentRunnerFactory:
    async def prepare(self, _snapshot: StrategySnapshot) -> object:
        raise RuntimeError("MX request failed: 余额不足")


def _run(run_id: int = 20260907101) -> StrategyRun:
    return StrategyRun(
        run_id=run_id,
        trigger_source=TriggerSource.SCHEDULED,
        schedule_id=1,
        snapshot=StrategySnapshot(
            prompt_version="v1",
            risk_rules_version="risk-v1",
        ),
    )


@pytest.mark.asyncio
async def test_a_failed_run_publishes_a_failure_notification() -> None:
    """A scheduled run failing overnight is exactly what needs announcing."""

    run = _run()
    repository = InMemoryRunRepository(run)
    notifier = RecordingNotifier()
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=ExplodingAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
        notifier=notifier,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError):
        await executor.execute(run.run_id)

    assert repository.run.status is RunStatus.FAILED
    assert [event.kind for event in notifier.published] == [
        NotificationEventKind.RUN_FAILED
    ]
    event = notifier.published[0]
    assert event.run_id == run.run_id
    assert "余额不足" in (event.failure_reason or "")


@pytest.mark.asyncio
async def test_a_user_requested_abort_stays_silent() -> None:
    """Aborting is deliberate, so it is not something to page the operator about."""

    run = _run()
    repository = InMemoryRunRepository(run)
    notifier = RecordingNotifier()
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=FailingAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
        notifier=notifier,  # type: ignore[arg-type]
    )

    async def abort_immediately() -> None:
        raise RunAbortError(run.run_id)

    executor.set_execution_guard(abort_immediately)

    with pytest.raises(RunAbortError):
        await executor.execute(run.run_id)

    assert repository.run.status is RunStatus.ABORTED
    assert notifier.published == []


@pytest.mark.asyncio
async def test_a_broken_notifier_does_not_mask_the_run_failure() -> None:
    class ExplodingNotifier:
        async def publish(self, event: NotificationEvent) -> None:
            raise RuntimeError("dispatcher is down")

    run = _run()
    repository = InMemoryRunRepository(run)
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=ExplodingAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
        notifier=ExplodingNotifier(),  # type: ignore[arg-type]
    )

    # The original failure must still surface, not the notifier's.
    with pytest.raises(RuntimeError, match="余额不足"):
        await executor.execute(run.run_id)
    assert repository.run.status is RunStatus.FAILED


class RecordingCompletionHook:
    def __init__(self) -> None:
        self.completed: list[int] = []

    async def on_run_completed(self, run_id: int) -> None:
        self.completed.append(run_id)


class StubAgentRunnerFactory:
    async def prepare(self, _snapshot: StrategySnapshot) -> object:
        class Runtime:
            tool_registry = object()
            stage_runtimes: dict[str, object] = {}

        return Runtime()


def _watch_run(run_id: int = 20260907301) -> StrategyRun:
    """A run whose ninth digit says 盯盘, started in the Watch state."""

    return StrategyRun(
        run_id=run_id,
        trigger_source=TriggerSource.SCHEDULED,
        schedule_id=1,
        snapshot=StrategySnapshot(
            prompt_version="v1",
            risk_rules_version="risk-v1",
        ),
        current_state=RunState.WATCH,
    )


def _stub_watch_orchestrator(run_id: int) -> type:
    """A watch is one stage: Watch straight to COMPLETED, no Summary."""

    class StubWatchOrchestrator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def execute(self, run: StrategyRun) -> RunResult:
            run.advance_to(RunState.COMPLETED, at=run.started_at + timedelta(seconds=9))
            return RunResult(
                run_id=run_id,
                status=RunStatus.COMPLETED,
                final_state=RunState.COMPLETED,
                summary="盯盘记录",
                # Deliberately not 9 seconds: nothing may announce this number.
                total_duration_ms=3,
            )

    return StubWatchOrchestrator


def _stub_orchestrator(run_id: int) -> type:
    """Stand in for the real pipeline so a run can simply succeed."""

    class StubOrchestrator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def execute(self, run: StrategyRun) -> RunResult:
            run.advance_to(RunState.SUMMARY)
            run.advance_to(
                RunState.COMPLETED, at=run.started_at + timedelta(seconds=42)
            )
            return RunResult(
                run_id=run_id,
                status=RunStatus.COMPLETED,
                final_state=RunState.COMPLETED,
                summary="<section>done</section>",
                # Deliberately not 42 seconds: nothing may announce this number.
                total_duration_ms=7,
            )

    return StubOrchestrator


def _stub_two_half_orchestrator(
    run_id: int,
    *,
    run_seconds: int,
    render_seconds: int,
) -> type:
    """An analysis the way the render lane really runs one: two halves.

    The account half parks the run in Summary with its Markdown saved; the
    HTML is rendered later on the other lane, in a second call whose own
    elapsed time is the render and nothing else.
    """

    class StubTwoHalfOrchestrator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def execute(self, run: StrategyRun) -> RunResult:
            run.set_summary("# 报告", render_mode="markdown")
            run.advance_to(RunState.SUMMARY)
            return RunResult(
                run_id=run_id,
                status=run.status,
                final_state=RunState.SUMMARY,
                summary=run.summary,
                total_duration_ms=run_seconds * 1_000,
                pending_report=RunReport(content="# 报告"),
            )

        async def render_summary(
            self, run: StrategyRun, _report: RunReport
        ) -> RunResult:
            run.set_summary("<section>报告</section>", render_mode="html")
            run.advance_to(
                RunState.COMPLETED,
                at=run.started_at + timedelta(seconds=run_seconds + render_seconds),
            )
            return RunResult(
                run_id=run_id,
                status=run.status,
                final_state=RunState.COMPLETED,
                summary=run.summary,
                total_duration_ms=render_seconds * 1_000,
            )

    return StubTwoHalfOrchestrator


@pytest.mark.asyncio
async def test_a_finished_run_reaches_the_completion_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    repository = InMemoryRunRepository(run)
    hook = RecordingCompletionHook()
    monkeypatch.setattr(
        "backend.business.runs.executor.AniuOrchestrator",
        _stub_orchestrator(run.run_id),
    )
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=StubAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
        run_completion_hook=hook,  # type: ignore[arg-type]
    )

    await executor.execute(run.run_id)

    assert hook.completed == [run.run_id]


@pytest.mark.asyncio
async def test_a_failed_run_never_reaches_the_completion_hook() -> None:
    """Away mode mails finished reports; a failure has none to mail."""

    run = _run()
    repository = InMemoryRunRepository(run)
    hook = RecordingCompletionHook()
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=ExplodingAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
        run_completion_hook=hook,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError):
        await executor.execute(run.run_id)

    assert repository.run.status is RunStatus.FAILED
    assert hook.completed == []


@pytest.mark.asyncio
async def test_an_aborted_run_never_reaches_the_completion_hook() -> None:
    run = _run()
    repository = InMemoryRunRepository(run)
    hook = RecordingCompletionHook()
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=FailingAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
        run_completion_hook=hook,  # type: ignore[arg-type]
    )

    async def abort_immediately() -> None:
        raise RunAbortError(run.run_id)

    executor.set_execution_guard(abort_immediately)

    with pytest.raises(RunAbortError):
        await executor.execute(run.run_id)

    assert hook.completed == []


@pytest.mark.asyncio
async def test_a_broken_completion_hook_does_not_fail_the_finished_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingHook:
        async def on_run_completed(self, run_id: int) -> None:
            raise RuntimeError("mailer is down")

    run = _run()
    repository = InMemoryRunRepository(run)
    monkeypatch.setattr(
        "backend.business.runs.executor.AniuOrchestrator",
        _stub_orchestrator(run.run_id),
    )
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=StubAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
        run_completion_hook=ExplodingHook(),  # type: ignore[arg-type]
    )

    # The run succeeded; a hook problem may not turn that into a failure.
    await executor.execute(run.run_id)


@pytest.mark.asyncio
async def test_a_finished_run_announces_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run that trades nothing sends no other event, so without this a quiet
    day looks exactly like a scheduler that stopped firing."""

    run = _run()
    repository = InMemoryRunRepository(run)
    notifier = RecordingNotifier()
    monkeypatch.setattr(
        "backend.business.runs.executor.AniuOrchestrator",
        _stub_orchestrator(run.run_id),
    )
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=StubAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
        notifier=notifier,  # type: ignore[arg-type]
    )

    await executor.execute(run.run_id)

    assert [event.kind for event in notifier.published] == [
        NotificationEventKind.RUN_COMPLETED
    ]
    event = notifier.published[0]
    assert event.run_id == run.run_id
    assert event.duration_ms == 42_000


@pytest.mark.asyncio
async def test_a_finished_watch_announces_itself_under_its_own_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """盯盘 finishes every few minutes, so it must not share 操盘's event.

    Sharing it would mean a channel that wants to hear a dozen analyses a day
    is signed up for eighty watches it never asked for.
    """

    run = _watch_run()
    repository = InMemoryRunRepository(run)
    notifier = RecordingNotifier()
    monkeypatch.setattr(
        "backend.business.runs.executor.AniuOrchestrator",
        _stub_watch_orchestrator(run.run_id),
    )
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=StubAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
        notifier=notifier,  # type: ignore[arg-type]
    )

    await executor.execute(run.run_id)

    assert [event.kind for event in notifier.published] == [
        NotificationEventKind.WATCH_COMPLETED
    ]
    assert notifier.published[0].run_id == run.run_id
    assert notifier.published[0].duration_ms == 9_000


@pytest.mark.asyncio
async def test_a_two_half_analysis_announces_the_whole_run_not_the_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The push has to say what the run detail page says.

    The page reads the run's own timestamps, so it covers both halves. The
    orchestrator's `total_duration_ms` covers one call, and on the render lane
    that call is the HTML render alone: on 2026-09-16 run 20260916112 read
    5 分 32 秒 on its page and pushed 27 秒, which was the render.
    """

    run = _run()
    repository = InMemoryRunRepository(run)
    notifier = RecordingNotifier()
    monkeypatch.setattr(
        "backend.business.runs.executor.AniuOrchestrator",
        _stub_two_half_orchestrator(run.run_id, run_seconds=305, render_seconds=27),
    )
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=StubAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
        notifier=notifier,  # type: ignore[arg-type]
    )

    executed = await executor.execute(run.run_id)

    # Parked, so there is nothing to announce yet.
    assert executed.pending_report is not None
    assert notifier.published == []

    await executor.render_summary(run.run_id, executed.pending_report)

    assert [event.kind for event in notifier.published] == [
        NotificationEventKind.RUN_COMPLETED
    ]
    assert notifier.published[0].duration_ms == 332_000


@pytest.mark.asyncio
async def test_a_failed_watch_is_announced_like_any_other_failure() -> None:
    """Failure is deliberately not split by task: a watch that cannot run is
    the plan going unexecuted, which is worth hearing at any frequency."""

    run = _watch_run()
    repository = InMemoryRunRepository(run)
    notifier = RecordingNotifier()
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=ExplodingAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
        notifier=notifier,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError):
        await executor.execute(run.run_id)

    assert [event.kind for event in notifier.published] == [
        NotificationEventKind.RUN_FAILED
    ]


@pytest.mark.asyncio
async def test_a_failed_run_is_not_also_announced_as_finished() -> None:
    run = _run()
    repository = InMemoryRunRepository(run)
    notifier = RecordingNotifier()
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=ExplodingAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
        notifier=notifier,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError):
        await executor.execute(run.run_id)

    assert NotificationEventKind.RUN_COMPLETED not in {
        event.kind for event in notifier.published
    }


@pytest.mark.asyncio
async def test_a_broken_notifier_does_not_fail_the_finished_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The announcement is a side effect of finishing, not part of finishing."""

    class ExplodingNotifier:
        async def publish(self, _event: NotificationEvent) -> None:
            raise RuntimeError("推送服务不可用")

    run = _run()
    repository = InMemoryRunRepository(run)
    monkeypatch.setattr(
        "backend.business.runs.executor.AniuOrchestrator",
        _stub_orchestrator(run.run_id),
    )
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=StubAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
        notifier=ExplodingNotifier(),  # type: ignore[arg-type]
    )

    executed = await executor.execute(run.run_id)

    assert executed.detail.status == RunStatus.COMPLETED.value


def _final_status_step(run: StrategyRun) -> tuple[str, TraceStep] | None:
    """The 「最终状态」 step and the key of the stage it was filed under."""

    for stage in run.trace.stages:
        for step in stage.steps:
            if step.title == "最终状态":
                return stage.key, step
    return None


@pytest.mark.asyncio
async def test_a_finished_analysis_files_its_final_status_under_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every finished run ends with a step saying so, and how long it took.

    This used to be written only when the runtime still held a recorder, and
    since the account and render halves were split nothing reaches here with
    one — the teardown runs first. The step simply stopped being recorded:
    75 of 75 runs had it on 2026-09-14, 39 of 78 on 09-15 as the split went
    live, 0 of 70 on 09-16.
    """

    run = _run()
    repository = InMemoryRunRepository(run)
    monkeypatch.setattr(
        "backend.business.runs.executor.AniuOrchestrator",
        _stub_orchestrator(run.run_id),
    )
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=StubAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
    )

    await executor.execute(run.run_id)

    filed = _final_status_step(repository.run)
    assert filed is not None
    stage_key, step = filed
    assert stage_key == "summary"
    assert step.status == "completed"
    # The whole run, the same number the push and the page carry.
    assert step.data == {"total_duration_ms": 42_000}


@pytest.mark.asyncio
async def test_a_finished_watch_files_its_final_status_on_its_own_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A watch has no Summary stage, and must not be given a hollow one.

    Filing this step under "Summary" regardless appended a second stage the
    run never entered. Being last, it became what the detail page treated as
    the terminal stage, so a watch's report hung off a stage that had never
    run. The watch closes its own stage now, so there is nothing to stand in
    for.
    """

    run = _watch_run()
    repository = InMemoryRunRepository(run)
    monkeypatch.setattr(
        "backend.business.runs.executor.AniuOrchestrator",
        _stub_watch_orchestrator(run.run_id),
    )
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=StubAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
    )

    await executor.execute(run.run_id)

    filed = _final_status_step(repository.run)
    assert filed is not None
    stage_key, step = filed
    assert stage_key == "watch"
    assert step.data == {"total_duration_ms": 9_000}
    assert [stage.key for stage in repository.run.trace.stages] == ["watch"]


@pytest.mark.asyncio
async def test_a_rendered_analysis_files_its_final_status_after_the_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The render lane finishes the run, so the step has to survive the split."""

    run = _run()
    repository = InMemoryRunRepository(run)
    monkeypatch.setattr(
        "backend.business.runs.executor.AniuOrchestrator",
        _stub_two_half_orchestrator(run.run_id, run_seconds=305, render_seconds=27),
    )
    executor = RunExecutor(
        repository,  # type: ignore[arg-type]
        agent_runner_factory=StubAgentRunnerFactory(),  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
    )

    executed = await executor.execute(run.run_id)
    # Parked: the run is not finished, so there is nothing final to say yet.
    assert _final_status_step(repository.run) is None

    assert executed.pending_report is not None
    await executor.render_summary(run.run_id, executed.pending_report)

    filed = _final_status_step(repository.run)
    assert filed is not None
    stage_key, step = filed
    assert stage_key == "summary"
    assert step.data == {"total_duration_ms": 332_000}
