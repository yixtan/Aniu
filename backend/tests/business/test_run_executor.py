"""Tests for worker execution fencing in the run executor."""

from __future__ import annotations

import pytest

from backend.business.notifications import (
    NotificationEvent,
    NotificationEventKind,
)
from backend.business.runs import StrategyRun, StrategySnapshot
from backend.business.runs.abort_registry import ActiveRunAbortRegistry
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
        assert registry.active_signal is not None
        raise RunAbortError(run.run_id)

    executor.set_execution_guard(reject_expired_claim)

    with pytest.raises(RunAbortError):
        await executor.execute(run.run_id)

    assert agent_factory.prepared is False
    assert repository.run.status is RunStatus.ABORTED
    assert registry.active_signal is None


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


def _stub_orchestrator(run_id: int) -> type:
    """Stand in for the real pipeline so a run can simply succeed."""

    class StubOrchestrator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def execute(self, run: StrategyRun) -> RunResult:
            run.advance_to(RunState.SUMMARY)
            run.advance_to(RunState.COMPLETED)
            return RunResult(
                run_id=run_id,
                status=RunStatus.COMPLETED,
                final_state=RunState.COMPLETED,
                summary="<section>done</section>",
                total_duration_ms=7,
            )

    return StubOrchestrator


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
    assert event.duration_ms == 7


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

    detail = await executor.execute(run.run_id)

    assert detail.status == RunStatus.COMPLETED.value
