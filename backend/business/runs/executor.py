"""Worker-side execution of an already-created strategy run."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from backend.business.away import RunCompletionHookPort
from backend.business.notifications import (
    NotificationEvent,
    NotificationEventKind,
    NotificationPublisherPort,
)
from backend.business.runs import StrategyRun
from backend.business.runs.abort_registry import ActiveRunAbortRegistry
from backend.business.runs.agent_runner import AgentRunnerFactoryPort
from backend.business.runs.callbacks import RunExecutionCallbacks
from backend.business.runs.dto import RunDetailDTO, to_run_detail_dto
from backend.business.runs.execution import RunReport
from backend.business.runs.numbering import is_order_watch_task
from backend.business.runs.orchestration import (
    AniuOrchestrator,
    CancellationsAccepted,
    RunResult,
)
from backend.business.runs.ports import (
    FollowedCompaniesPort,
    OrderPlanPort,
    RunRepositoryPort,
)
from backend.business.runs.runtime import RunRuntimeState
from backend.business.runs.trace_support import (
    RunSnapshotPublisher,
    RunTraceSupport,
)
from backend.business.shared import CommitterPort, RunAbortError, RunNotFoundError
from backend.business.shared.enums import RunState, RunStatus
from backend.business.shared.stock_api_source import (
    STOCK_API_SOURCE_RUN,
    stock_api_source,
)

NowProvider = Callable[[], datetime]
MarketSessionOpen = Callable[[datetime], bool]
TraceStepDeltaPublisher = Callable[..., Awaitable[None]]
ExecutionGuard = Callable[[], Awaitable[None]]
ExecutionFence = Callable[[StrategyRun], Awaitable[bool]]
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExecutedRun:
    """The outcome of the part of a run that holds the trading account."""

    detail: RunDetailDTO
    pending_report: RunReport | None = None
    """Set when an analysis is parked in Summary awaiting its HTML render."""


class RunExecutor:
    """Execute one durable run job; no request-side creation or query behavior."""

    def __init__(
        self,
        run_repo: RunRepositoryPort,
        *,
        agent_runner_factory: AgentRunnerFactoryPort,
        abort_registry: ActiveRunAbortRegistry,
        committer: CommitterPort | None = None,
        snapshot_publisher: RunSnapshotPublisher | None = None,
        trace_step_delta_publisher: TraceStepDeltaPublisher | None = None,
        now_provider: NowProvider | None = None,
        market_session_is_open: MarketSessionOpen | None = None,
        cancellations_accepted: CancellationsAccepted | None = None,
        notifier: NotificationPublisherPort | None = None,
        run_completion_hook: RunCompletionHookPort | None = None,
        watchlist: FollowedCompaniesPort | None = None,
        order_plan: OrderPlanPort | None = None,
    ) -> None:
        self._run_repo = run_repo
        self._committer = committer
        self._execution_guard: ExecutionGuard | None = None
        self._execution_fence: ExecutionFence | None = None
        self._agent_runner_factory = agent_runner_factory
        self._abort_registry = abort_registry
        self._now_provider = now_provider or (lambda: datetime.now(tz=UTC))
        self._market_session_is_open = market_session_is_open or (lambda _moment: False)
        self._cancellations_accepted = cancellations_accepted
        self._runtime = RunRuntimeState()
        self._notifier = notifier
        self._watchlist = watchlist
        self._order_plan = order_plan
        self._run_completion_hook = run_completion_hook
        self._execution_callbacks = RunExecutionCallbacks(
            runtime=self._runtime,
            publish_trace_step_delta=trace_step_delta_publisher,
            notifier=notifier,
        )
        self._trace = RunTraceSupport(
            run_repo=run_repo,
            committer=committer,
            snapshot_publisher=snapshot_publisher,
        )

    async def _with_run_runtime(
        self,
        run: StrategyRun,
        *,
        phase: str,
        work: Callable[[AniuOrchestrator], Awaitable[RunResult]],
    ) -> RunResult:
        """Set up the recorder, abort signal and agent runtime, then run one half.

        Both halves of an analysis need this scaffolding and the second half
        runs on another lane in its own session, so it cannot inherit what the
        first one built. `phase` only names the half in the log.
        """

        self._activate_runtime(run)
        self._runtime.trace_recorder = self._trace.make_recorder(
            run,
            persist_run=self._persist_run,
        )
        self._execution_callbacks.bind_runtime(self._runtime)
        abort_signal = self._abort_registry.activate(run.run_id)
        started_at = perf_counter()
        logger.info(
            "run_execution_started",
            extra={
                "run_id": run.run_id,
                "job_id": run.run_id,
                "stage_id": run.trace.current_stage_id or run.current_state.value,
                "trigger_source": run.trigger_source.value,
                "current_state": run.current_state.value,
                "phase": phase,
                "status": "running",
            },
        )
        try:
            if self._execution_guard is not None:
                await self._execution_guard()
            agent_runtime = await self._agent_runner_factory.prepare(run.snapshot)
            with stock_api_source(STOCK_API_SOURCE_RUN):
                return await work(
                    AniuOrchestrator(
                        state_callbacks=self._execution_callbacks,
                        agent_runner_factory=self._agent_runner_factory,
                        tool_registry=agent_runtime.tool_registry,
                        stage_runtimes=agent_runtime.stage_runtimes,
                        abort_signal=abort_signal,
                        market_session_is_open=self._market_session_is_open,
                        cancellations_accepted=self._cancellations_accepted,
                        now_provider=self._now_provider,
                        watchlist=self._watchlist,
                        order_plan=self._order_plan,
                    )
                )
        except RunAbortError as exc:
            logger.warning(
                "run_execution_aborted",
                extra={
                    "run_id": run.run_id,
                    "job_id": run.run_id,
                    "stage_id": run.trace.current_stage_id or run.current_state.value,
                    "duration_ms": int((perf_counter() - started_at) * 1000),
                    "error_code": "RUN_ABORTED",
                    "current_state": run.current_state.value,
                    "phase": phase,
                    "status": "aborted",
                },
            )
            await self._record_aborted_run(run, exc)
            raise
        except Exception as exc:
            logger.warning(
                "run_execution_failed",
                extra={
                    "run_id": run.run_id,
                    "job_id": run.run_id,
                    "stage_id": run.trace.current_stage_id or run.current_state.value,
                    "duration_ms": int((perf_counter() - started_at) * 1000),
                    "error_code": type(exc).__name__,
                    "current_state": run.current_state.value,
                    "phase": phase,
                    "status": "failed",
                },
                exc_info=True,
            )
            await self._record_failed_run(run, exc)
            raise
        finally:
            recorder = self._runtime.trace_recorder
            if recorder is not None:
                await recorder.aclose()
            self._reset_runtime()
            self._abort_registry.clear(abort_signal)

    async def execute(self, run_id: int) -> ExecutedRun:
        """Execute the part of a run that holds the trading account.

        A watch finishes here. An analysis stops after its Run stage with the
        report saved as Markdown and the run parked in Summary; the returned
        `pending_report` is what the render lane needs to finish it.
        """

        run = await self._run_repo.get_by_id(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        if run.status is not RunStatus.RUNNING:
            return ExecutedRun(detail=to_run_detail_dto(run))

        result = await self._with_run_runtime(
            run,
            phase="account",
            work=lambda orchestrator: orchestrator.execute(run),
        )
        if result.pending_report is not None:
            logger.info(
                "run_account_phase_completed",
                extra={
                    "run_id": run.run_id,
                    "job_id": run.run_id,
                    "stage_id": RunState.RUN.value,
                    "duration_ms": result.total_duration_ms,
                    "current_state": run.current_state.value,
                    "status": "awaiting_summary",
                },
            )
            stored = await self._reload(run.run_id)
            return ExecutedRun(
                detail=to_run_detail_dto(stored),
                pending_report=result.pending_report,
            )

        await self._record_final_status_step(run, result)
        stored = await self._reload(run.run_id)
        # Both live outside the try: a side effect of finishing must not be
        # caught by the handler that decides a run failed.
        await self._notify_run_completed(run.run_id)
        await self._announce_run_completed(run, result.total_duration_ms)
        return ExecutedRun(detail=to_run_detail_dto(stored))

    async def render_summary(self, run_id: int, report: RunReport) -> RunDetailDTO:
        """Finish a parked analysis by rendering its HTML summary.

        Runs off the exclusive lane, so an order watch may start, act and
        finish while this is still going. Safe because the Summary stage
        reaches no tool that can trade or cancel — a fact pinned by a test
        rather than left to this comment.
        """

        run = await self._run_repo.get_by_id(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        if run.status is not RunStatus.RUNNING or run.current_state is not (
            RunState.SUMMARY
        ):
            return to_run_detail_dto(run)

        result = await self._with_run_runtime(
            run,
            phase="summary",
            work=lambda orchestrator: orchestrator.render_summary(run, report),
        )
        await self._record_final_status_step(run, result)
        logger.info(
            "run_execution_completed",
            extra={
                "run_id": run.run_id,
                "job_id": run.run_id,
                "stage_id": run.trace.current_stage_id or "Summary",
                "duration_ms": result.total_duration_ms,
                "current_state": run.current_state.value,
                "status": "completed",
            },
        )
        stored = await self._reload(run.run_id)
        await self._notify_run_completed(run.run_id)
        await self._announce_run_completed(run, result.total_duration_ms)
        return to_run_detail_dto(stored)

    async def _record_final_status_step(
        self,
        run: StrategyRun,
        result: RunResult,
    ) -> None:
        recorder = self._runtime.trace_recorder
        if recorder is not None:
            # The recorder persists the already-terminal run and final trace
            # together, so callers never observe COMPLETED without this step.
            # Always "Summary", including for a watch, which has no such
            # stage of its own: the recorder then appends a one-step stage,
            # and that trailing stage is what the run detail page treats as
            # the final report. Changing it here would move a watch's report.
            await recorder.set_status_step(
                "Summary",
                title="最终状态",
                summary="运行已完成。",
                data={"total_duration_ms": result.total_duration_ms},
            )
        else:
            await self._persist_run(run)
            await self._commit()

    async def _reload(self, run_id: int) -> StrategyRun:
        stored = await self._run_repo.get_by_id(run_id)
        if stored is None:
            raise RunNotFoundError(run_id)
        return stored

    async def cancel(self, run_id: int, reason: str) -> None:
        run = await self._run_repo.get_by_id(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        if run.status is not RunStatus.RUNNING:
            return
        self._activate_runtime(run)
        self._runtime.trace_recorder = self._trace.make_recorder(
            run,
            persist_run=self._persist_run,
        )
        self._execution_callbacks.bind_runtime(self._runtime)
        signal = self._abort_registry.activate(run_id)
        signal.abort(reason)
        try:
            await self._record_aborted_run(run, RunAbortError(run_id))
        finally:
            self._reset_runtime()
            self._abort_registry.clear(signal)

    async def _record_aborted_run(
        self,
        run: StrategyRun,
        exc: RunAbortError,
    ) -> None:
        if run.status is not RunStatus.RUNNING:
            return
        previous_state = run.current_state.value
        run.abort()
        await self._persist_run(run)
        await self._commit()
        recorder = self._runtime.trace_recorder
        if recorder is None:
            return
        if previous_state != RunState.FAILED.value:
            await recorder.fail_stage(previous_state, f"运行已中止：{exc}")

    async def _record_failed_run(
        self,
        run: StrategyRun,
        exc: Exception,
    ) -> None:
        if run.status is not RunStatus.RUNNING:
            return
        failure_reason = str(exc).strip() or type(exc).__name__
        try:
            previous_state = run.current_state.value
            run.fail(failure_reason)
            await self._persist_run(run)
            await self._commit()
            recorder = self._runtime.trace_recorder
            if recorder is None:
                return
            if previous_state != RunState.FAILED.value:
                await recorder.fail_stage(previous_state, f"运行失败：{failure_reason}")
        except Exception:
            logger.exception(
                "failed to persist run failure state",
                extra={"run_id": run.run_id},
            )
        # Announced outside the block above so a persistence problem still
        # reaches the operator. A user-requested abort is deliberately silent.
        await self._notify_run_failed(run, failure_reason)

    async def _notify_run_completed(self, run_id: int) -> None:
        """Let away mode act on a finished run without affecting the run."""

        if self._run_completion_hook is None:
            return
        try:
            await self._run_completion_hook.on_run_completed(run_id)
        except Exception:
            logger.warning(
                "run completion hook failed",
                extra={"run_id": run_id},
                exc_info=True,
            )

    async def _announce_run_completed(self, run: StrategyRun, duration_ms: int) -> None:
        """Announce a finished run without letting the push affect the run.

        An analysis that traded nothing sends no other notification, so without
        this a quiet day is indistinguishable from a scheduler that stopped
        firing. A watch is the opposite case — it finishes every few minutes
        all session — so it announces itself under its own event kind, which a
        channel subscribes to separately and by default does not.

        Which one this is comes from the task id rather than the run's state,
        because by now the state is COMPLETED either way.
        """

        await self._publish_run_event(
            NotificationEvent(
                kind=(
                    NotificationEventKind.WATCH_COMPLETED
                    if is_order_watch_task(run.run_id)
                    else NotificationEventKind.RUN_COMPLETED
                ),
                run_id=run.run_id,
                stage_name=run.trace.current_stage_id or run.current_state.value,
                duration_ms=duration_ms,
            )
        )

    async def _publish_run_event(self, event: NotificationEvent) -> None:
        if self._notifier is None:
            return
        try:
            await self._notifier.publish(event)
        except Exception:
            logger.warning(
                "failed to publish run notification",
                extra={"run_id": event.run_id, "event": event.kind.value},
                exc_info=True,
            )

    async def _notify_run_failed(self, run: StrategyRun, reason: str) -> None:
        """Announce a failed run without letting the push affect the run."""

        await self._publish_run_event(
            NotificationEvent(
                kind=NotificationEventKind.RUN_FAILED,
                run_id=run.run_id,
                stage_name=run.trace.current_stage_id or run.current_state.value,
                failure_reason=reason,
            )
        )

    async def _persist_run(self, run: StrategyRun) -> StrategyRun:
        if self._execution_fence is None:
            return await self._run_repo.save(run)
        if not await self._execution_fence(run):
            raise RunAbortError(run.run_id)
        return run

    async def _commit(self) -> None:
        if self._execution_guard is not None:
            await self._execution_guard()
        if self._committer is not None:
            await self._committer.commit()

    def set_execution_fence(self, fence: ExecutionFence) -> None:
        """Require every run persistence write to match the live claim."""

        self._execution_fence = fence

    def set_execution_guard(self, guard: ExecutionGuard) -> None:
        """Require a live worker claim before execution and persistence commits."""

        self._execution_guard = guard

    def _activate_runtime(self, run: StrategyRun) -> None:
        self._runtime = RunRuntimeState(active_run=run)
        self._execution_callbacks.bind_runtime(self._runtime)

    def _reset_runtime(self) -> None:
        self._runtime = RunRuntimeState()
        self._execution_callbacks.bind_runtime(self._runtime)
