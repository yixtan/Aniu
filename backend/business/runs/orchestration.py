"""Two-stage orchestration: Run followed by optional HTML Summary."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Protocol, cast

from backend.business.order_directives import OrderDirective
from backend.business.runs import StrategyRun
from backend.business.runs.abort import RunAbortSignal
from backend.business.runs.agent_runner import AgentRunnerFactoryPort
from backend.business.runs.execution import (
    LlmStreamDeltaSink,
    RunExecutionContext,
    RunReport,
    StagePromptPreparedSink,
    SummaryDraft,
    ToolLoopEventSink,
)
from backend.business.runs.ports import FollowedCompaniesPort, OrderPlanPort
from backend.business.runs.run_events import RunEventType
from backend.business.runs.stages.run_stage import RunStage
from backend.business.runs.stages.summary_stage import SummaryStage
from backend.business.runs.stages.watch_stage import WatchStage
from backend.business.shared import RunAbortError, ServiceConfigurationError
from backend.business.shared.enums import RunState, RunStatus

logger = logging.getLogger(__name__)

StageOutput = RunReport | SummaryDraft
MarketSessionOpen = Callable[[datetime], bool]
CancellationsAccepted = Callable[[datetime], bool]
NowProvider = Callable[[], datetime]
MAX_SUMMARY_ATTEMPTS = 2

WATCH_DEADLINE_SECONDS = 60.0
"""How long a watch may hold the worker before it is given up on.

A watch is worth three minutes of coverage; an analysis is worth twenty and
writes the plan the watch reads. So when one of them has to go, it is the
watch — and the way that is enforced is by never letting a watch still be
running when the analysis it would block arrives.

The bound is derivable rather than chosen. A watch starts at most 50 seconds
before an analysis does (the closest the two grids come, at 3 and 20 minutes),
and an analysis will wait `ANALYSIS_WAITS_FOR_WATCH_SECONDS` for a watch to
finish, so a watch that always ends within 50 + 30 = 80 seconds can never cost
an analysis its slot. Sixty leaves margin; a test re-derives the ceiling.

This is not a timeout on the HTTP read. On 2026-09-14 a single call took 304
seconds to deliver 214 tokens — the socket was alive the whole time, dribbling,
so no read timeout would ever have fired. What is bounded here is wall clock.
"""


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: int
    status: RunStatus
    final_state: RunState
    summary: str | None
    total_duration_ms: int
    pending_report: RunReport | None = None
    """Set when the run stopped at Summary and still owes an HTML render.

    Handed to `render_summary` rather than rebuilt from the trace: the report
    carries the raw tool evidence the summary reads, and the trace stores a
    slimmed projection of it. Living only in memory is what makes a crash
    here cost the HTML and nothing else — the Markdown was saved first.
    """


class StateCallbacks(Protocol):
    async def on_state_entered(self, run_id: int, state_name: str) -> None: ...

    async def on_state_completed(
        self,
        run_id: int,
        state_name: str,
        state_output: dict[str, object],
        duration_ms: int,
    ) -> None: ...

    async def on_state_skipped(
        self, run_id: int, state_name: str, summary: str
    ) -> None: ...

    async def on_state_degraded(
        self, run_id: int, state_name: str, summary: str
    ) -> None: ...

    async def on_stage_prompt_prepared(
        self, run_id: int, stage_name: str, payload: dict[str, object]
    ) -> None: ...

    async def on_tool_loop_event(
        self,
        run_id: int,
        stage_name: str,
        event_type: RunEventType,
        payload: dict[str, object],
    ) -> None: ...

    async def on_llm_stream_delta(
        self,
        run_id: int,
        stage_name: str,
        delta: str,
        channel: str = "text",
    ) -> None: ...


class AniuOrchestrator:
    """Execute one Run agent and then build an optional HTML presentation."""

    def __init__(
        self,
        *,
        state_callbacks: StateCallbacks,
        agent_runner_factory: AgentRunnerFactoryPort,
        tool_registry: object | None = None,
        llm_runtime: object | None = None,
        stage_runtimes: dict[str, object] | None = None,
        run_stage: RunStage | None = None,
        summary_stage: SummaryStage | None = None,
        watch_stage: WatchStage | None = None,
        abort_signal: RunAbortSignal | None = None,
        market_session_is_open: MarketSessionOpen,
        cancellations_accepted: CancellationsAccepted | None = None,
        now_provider: NowProvider | None = None,
        watchlist: FollowedCompaniesPort | None = None,
        order_plan: OrderPlanPort | None = None,
        watch_deadline_seconds: float = WATCH_DEADLINE_SECONDS,
    ) -> None:
        self._callbacks = state_callbacks
        self._agent_runner_factory = agent_runner_factory
        self._tool_registry = tool_registry
        self._llm_runtime = llm_runtime
        self._stage_runtimes = stage_runtimes or {}
        self._run_stage = run_stage or RunStage()
        self._summary_stage = summary_stage or SummaryStage()
        self._watch_stage = watch_stage or WatchStage()
        self._abort_signal = abort_signal
        self._market_session_is_open = market_session_is_open
        self._cancellations_accepted = cancellations_accepted
        self._now_provider = now_provider or (lambda: datetime.now(tz=UTC))
        self._watchlist = watchlist
        self._order_plan = order_plan
        self._watch_deadline_seconds = watch_deadline_seconds

    async def _followed_companies(self, run_id: int) -> tuple[tuple[str, str], ...]:
        """Read the watchlist without letting it stop a run.

        It is a reference, not an input the run depends on: a database hiccup
        here should cost the run that reference, not the run.
        """

        if self._watchlist is None:
            return ()
        try:
            return await self._watchlist.followed()
        except Exception:
            logger.warning(
                "failed to read the watchlist for a run",
                extra={"run_id": run_id},
                exc_info=True,
            )
            return ()

    async def _build_context(
        self,
        run: StrategyRun,
        abort_signal: RunAbortSignal,
        *,
        read_watchlist: bool,
    ) -> RunExecutionContext:
        context = RunExecutionContext(run=run, snapshot=run.snapshot)
        context.abort_signal = abort_signal
        context.market_session_is_open = self._is_market_session_open
        context.cancellations_accepted = self._are_cancellations_accepted
        context.tool_registry = self._tool_registry
        if read_watchlist:
            context.followed_companies = await self._followed_companies(run.run_id)
        self._attach_runtime_callbacks(context, run.run_id)
        return context

    async def execute(self, run: StrategyRun) -> RunResult:
        """Do everything that holds the trading account, and stop there.

        For a watch that is the whole run: one stage, then COMPLETED. For an
        analysis it is the Run stage alone — the Markdown report is saved, the
        run is parked in Summary, and turning it into HTML is left to
        `render_summary`, which runs off the exclusive lane.

        That split is the point. Summary appears in neither the trade tool's
        nor the cancel tool's enabled stages and runs no tool loop at all, so
        a run waiting to be rendered holds nothing an order watch needs. Before
        the split, a healthy 75-second render blocked every watch that fell
        inside it — about two slots a day, on top of the pathological ones.
        """

        started_at = perf_counter()
        abort_signal = self._abort_signal or RunAbortSignal(run.run_id)
        context = await self._build_context(run, abort_signal, read_watchlist=True)

        if run.current_state is RunState.WATCH:
            return await self._execute_watch(run, context, started_at, abort_signal)

        await self._attach_order_plan_for_review(context)
        report = await self._execute_run_stage(context)
        context.run_report = report
        run.set_summary(report.content, render_mode="markdown")

        run.advance_to(RunState.SUMMARY)
        await self._callbacks.on_state_entered(run.run_id, RunState.SUMMARY.value)
        return RunResult(
            run_id=run.run_id,
            status=run.status,
            final_state=run.current_state,
            summary=run.summary,
            total_duration_ms=int((perf_counter() - started_at) * 1000),
            pending_report=report,
        )

    async def render_summary(self, run: StrategyRun, report: RunReport) -> RunResult:
        """Turn a parked run's report into HTML and complete it.

        Failing here is not failing the run. The Markdown was set before the
        account was released, so the outcome of a bad render is a run that
        completes with a plainer report — recorded as a degraded stage and as
        `summary_render_mode = markdown`, both visible — rather than a run
        that is lost after its trades were already placed.
        """

        started_at = perf_counter()
        abort_signal = self._abort_signal or RunAbortSignal(run.run_id)
        context = await self._build_context(run, abort_signal, read_watchlist=False)
        context.run_report = report
        try:
            summary = await self._generate_summary(context)
        except RunAbortError:
            raise
        except Exception as exc:
            fallback_reason = f"HTML 总结生成失败，已回退 Markdown：{exc}"
            await self._callbacks.on_state_degraded(
                run.run_id,
                RunState.SUMMARY.value,
                fallback_reason,
            )
        else:
            run.set_summary(summary.summary, render_mode="html")
            await self._callbacks.on_state_completed(
                run.run_id,
                RunState.SUMMARY.value,
                summary.as_payload(),
                0,
            )

        run.advance_to(RunState.COMPLETED)
        await self._callbacks.on_state_entered(run.run_id, RunState.COMPLETED.value)
        return RunResult(
            run_id=run.run_id,
            status=run.status,
            final_state=run.current_state,
            summary=run.summary,
            total_duration_ms=int((perf_counter() - started_at) * 1000),
        )

    async def _give_up_on_the_watch_at_the_deadline(
        self, run_id: int, signal: RunAbortSignal
    ) -> None:
        """Arm the run's own abort signal, rather than cancelling the task.

        The drivers await each stream chunk through `await_with_abort`, so an
        aborted watch stops at the next chunk — sub-second even on a provider
        that is only dribbling. Going through the existing signal also means
        the run unwinds the way a stopped run already does: ABORTED, the stage
        marked with the reason, and deliberately no push, since an abort is not
        a failure anyone needs waking for.
        """

        await asyncio.sleep(self._watch_deadline_seconds)
        logger.warning(
            "order watch gave up its slot",
            extra={
                "run_id": run_id,
                "deadline_seconds": self._watch_deadline_seconds,
            },
        )
        signal.abort(
            f"盯盘超过 {int(self._watch_deadline_seconds)} 秒未完成，"
            "已让位以免挡住操盘"
        )

    async def _execute_watch(
        self,
        run: StrategyRun,
        context: RunExecutionContext,
        started_at: float,
        abort_signal: RunAbortSignal,
    ) -> RunResult:
        """One stage, then done. There is no report to render.

        The plan is read here and the authorization set derived from it, so
        the authorizer refusing a write against an unnamed order and the model
        being told about that order are the same list — not two that could
        drift apart.
        """

        directives = await self._current_order_plan(run.run_id)
        context.authorized_order_ids = frozenset(
            item.order_id for item in directives
        )
        runtime = self._runtime_for(RunState.WATCH.value)
        if runtime is None:
            raise ServiceConfigurationError("Watch llm runtime is not configured")
        context.llm_runtime = runtime
        runner = self._agent_runner_factory.create(
            context, label=RunState.WATCH.value, runtime=runtime
        )
        await self._callbacks.on_state_entered(run.run_id, RunState.WATCH.value)
        deadline = asyncio.create_task(
            self._give_up_on_the_watch_at_the_deadline(run.run_id, abort_signal),
            name=f"watch-deadline:{run.run_id}",
        )
        try:
            report = cast(
                RunReport,
                await self._execute_stage(
                    run_id=run.run_id,
                    state_name=RunState.WATCH,
                    context=context,
                    execute=lambda: self._watch_stage.execute(
                        context, runner, directives=directives
                    ),
                ),
            )
        finally:
            deadline.cancel()
            with suppress(asyncio.CancelledError):
                await deadline
        run.set_summary(report.content, render_mode="markdown")
        run.advance_to(RunState.COMPLETED)
        await self._callbacks.on_state_entered(run.run_id, RunState.COMPLETED.value)
        return RunResult(
            run_id=run.run_id,
            status=run.status,
            final_state=run.current_state,
            summary=run.summary,
            total_duration_ms=int((perf_counter() - started_at) * 1000),
        )

    async def _current_order_plan(self, run_id: int) -> tuple[OrderDirective, ...]:
        """Read the plan; a failure here is the plan being empty, not a crash.

        An empty plan forbids every write, so a read that fails degrades to
        the safe side on its own. It is still logged, because a watch that
        silently could not read its plan looks exactly like one that had none.
        """

        if self._order_plan is None:
            return ()
        try:
            return await self._order_plan.current()
        except Exception:
            logger.warning(
                "failed to read the order plan for a watch",
                extra={"run_id": run_id},
                exc_info=True,
            )
            return ()

    async def _attach_order_plan_for_review(
        self, context: RunExecutionContext
    ) -> None:
        """Show an analysis the plan it is about to replace.

        Failing to read it costs context and nothing else. That is the opposite
        of the watch, where an unreadable plan has to mean "touch nothing" —
        here the plan authorizes no action, it only says what the last analysis
        concluded, so an empty one leaves this run exactly as blind as every
        run was before this existed.
        """

        if self._order_plan is None:
            return
        try:
            context.standing_order_plan = await self._order_plan.current()
            context.previous_order_plan = await self._order_plan.previous()
        except Exception:
            logger.warning(
                "failed to read the order plan for an analysis",
                extra={"run_id": context.run.run_id},
                exc_info=True,
            )

    async def _execute_run_stage(self, context: RunExecutionContext) -> RunReport:
        runtime = self._runtime_for(RunState.RUN.value)
        if runtime is None:
            raise ServiceConfigurationError("Run llm runtime is not configured")
        context.llm_runtime = runtime
        runner = self._agent_runner_factory.create(
            context, label=RunState.RUN.value, runtime=runtime
        )
        run = context.run
        await self._callbacks.on_state_entered(run.run_id, RunState.RUN.value)
        return cast(
            RunReport,
            await self._execute_stage(
                run_id=run.run_id,
                state_name=RunState.RUN,
                context=context,
                execute=lambda: self._run_stage.execute(context, runner),
            ),
        )

    async def _generate_summary(self, context: RunExecutionContext) -> SummaryDraft:
        runtime = self._runtime_for(RunState.SUMMARY.value)
        if runtime is None:
            raise ServiceConfigurationError("Summary llm runtime is not configured")
        context.llm_runtime = runtime
        runner = self._agent_runner_factory.create(
            context, label=RunState.SUMMARY.value, runtime=runtime
        )
        last_error: Exception | None = None
        for _attempt in range(MAX_SUMMARY_ATTEMPTS):
            try:
                return await self._summary_stage.execute(context, runner)
            except RunAbortError:
                raise
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def _execute_stage(
        self,
        *,
        run_id: int,
        state_name: RunState,
        context: RunExecutionContext,
        execute: Callable[[], Awaitable[StageOutput]],
    ) -> StageOutput:
        abort_signal = context.abort_signal
        if isinstance(abort_signal, RunAbortSignal):
            abort_signal.throw_if_aborted()
        started_at = perf_counter()
        output = await execute()
        if isinstance(abort_signal, RunAbortSignal):
            abort_signal.throw_if_aborted()
        await self._callbacks.on_state_completed(
            run_id,
            state_name.value,
            output.as_payload(),
            int((perf_counter() - started_at) * 1000),
        )
        return output

    def _runtime_for(self, stage_name: str) -> object | None:
        return self._stage_runtimes.get(stage_name, self._llm_runtime)

    def _are_cancellations_accepted(self) -> bool:
        """Default to the session when nobody supplied the narrower answer.

        Reading it wrong in the permissive direction only restores the old
        behaviour — the exchange still refuses, and the run is no worse off
        than before this existed. Reading it wrong the other way would stop
        a cancel that was still allowed, which is the expensive mistake.
        """

        if self._cancellations_accepted is None:
            return self._is_market_session_open()
        try:
            return self._cancellations_accepted(self._now_provider())
        except Exception:
            return self._is_market_session_open()

    def _is_market_session_open(self) -> bool:
        try:
            return self._market_session_is_open(self._now_provider())
        except Exception:
            return False

    def _attach_runtime_callbacks(
        self,
        context: RunExecutionContext,
        run_id: int,
    ) -> None:
        async def emit_tool_loop_event(
            stage_name: str,
            event_type: RunEventType,
            payload: dict[str, object],
        ) -> None:
            await self._callbacks.on_tool_loop_event(
                run_id, stage_name, event_type, payload
            )

        async def emit_llm_stream_delta(
            stage_name: str,
            delta: str,
            channel: str = "text",
        ) -> None:
            await self._callbacks.on_llm_stream_delta(
                run_id, stage_name, delta, channel
            )

        async def emit_stage_prompt_prepared(
            stage_name: str,
            payload: dict[str, object],
        ) -> None:
            await self._callbacks.on_stage_prompt_prepared(run_id, stage_name, payload)

        context.tool_loop_event_sink = cast(ToolLoopEventSink, emit_tool_loop_event)
        context.llm_stream_delta_sink = cast(LlmStreamDeltaSink, emit_llm_stream_delta)
        context.stage_prompt_prepared_sink = cast(
            StagePromptPreparedSink, emit_stage_prompt_prepared
        )
