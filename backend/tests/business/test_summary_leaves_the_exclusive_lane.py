"""The rule that lets a watch run while an analysis renders its summary."""

from __future__ import annotations

import pytest

from backend.business.runs import ACCOUNT_BOUND_STATES
from backend.business.runs.abort_registry import ActiveRunAbortRegistry
from backend.business.runs.execution import SummaryDraft
from backend.business.shared.enums import RunState, RunStatus
from backend.tests.business.test_aniu_orchestrator import (
    RecordingCallbacks,
    ScriptedSummaryStage,
    StaticRunStage,
    make_orchestrator,
    make_run,
)


def test_only_account_bound_states_can_reach_a_tool_that_trades() -> None:
    """The claim the whole split rests on, checked against the tools themselves.

    The render lane is safe because Summary cannot place or cancel an order.
    That is true today by virtue of two tuples in the tool layer, and nothing
    stops a later stage from being added to them. If one ever is, it has to
    become account-bound in the same change or a watch could run beside a
    stage that trades.
    """

    from backend.infra.integrations.mx_agent_tools import (
        TRADE_STAGES,
        WATCH_TRADE_STAGES,
    )

    can_act = set(TRADE_STAGES) | set(WATCH_TRADE_STAGES)
    account_bound = {state.value for state in ACCOUNT_BOUND_STATES}

    assert can_act <= account_bound, (
        f"stages that can trade but do not hold the account: {can_act - account_bound}"
    )
    assert RunState.SUMMARY.value not in can_act


def test_summary_is_not_account_bound_but_run_and_watch_are() -> None:
    assert ACCOUNT_BOUND_STATES == frozenset({RunState.RUN, RunState.WATCH})


@pytest.mark.asyncio
async def test_an_analysis_stops_holding_the_account_when_its_run_stage_ends() -> None:
    callbacks = RecordingCallbacks()
    summary_stage = ScriptedSummaryStage([SummaryDraft(summary="<p>rendered</p>")])
    run = make_run()

    result = await make_orchestrator(
        callbacks,
        run_stage=StaticRunStage(),
        summary_stage=summary_stage,
    ).execute(run)

    # Parked, not finished: the report exists, the HTML does not yet.
    assert result.final_state is RunState.SUMMARY
    assert run.status is RunStatus.RUNNING
    assert run.current_state is RunState.SUMMARY
    assert run.summary == "# Run report\n\nNo trade."
    assert run.summary_render_mode == "markdown"
    # The render lane has not been asked to do anything yet.
    assert summary_stage.calls == 0
    assert callbacks.entered == ["1:Run", "1:Summary"]
    # And the report travels with the result, because the trace keeps only a
    # slimmed projection of the evidence the summary needs.
    assert result.pending_report is not None
    assert result.pending_report.content == "# Run report\n\nNo trade."


@pytest.mark.asyncio
async def test_rendering_completes_the_parked_run() -> None:
    callbacks = RecordingCallbacks()
    summary_stage = ScriptedSummaryStage([SummaryDraft(summary="<p>rendered</p>")])
    orchestrator = make_orchestrator(
        callbacks,
        run_stage=StaticRunStage(),
        summary_stage=summary_stage,
    )
    run = make_run()

    parked = await orchestrator.execute(run)
    assert parked.pending_report is not None
    result = await orchestrator.render_summary(run, parked.pending_report)

    assert result.status is RunStatus.COMPLETED
    assert run.summary == "<p>rendered</p>"
    assert run.summary_render_mode == "html"
    assert callbacks.entered == ["1:Run", "1:Summary", "1:Completed"]


@pytest.mark.asyncio
async def test_a_failed_render_still_completes_the_run_with_its_markdown() -> None:
    """Losing the HTML must not lose a run whose trades already happened."""

    callbacks = RecordingCallbacks()
    orchestrator = make_orchestrator(
        callbacks,
        run_stage=StaticRunStage(),
        summary_stage=ScriptedSummaryStage(
            [RuntimeError("relay down"), RuntimeError("relay still down")]
        ),
    )
    run = make_run()

    parked = await orchestrator.execute(run)
    assert parked.pending_report is not None
    result = await orchestrator.render_summary(run, parked.pending_report)

    assert result.status is RunStatus.COMPLETED
    assert run.summary == "# Run report\n\nNo trade."
    assert run.summary_render_mode == "markdown"
    assert len(callbacks.degraded) == 1


def test_two_runs_in_flight_can_each_be_aborted() -> None:
    """A watch runs beside a render, so one signal slot is one too few."""

    registry = ActiveRunAbortRegistry()
    rendering = registry.activate(20260915101)
    watching = registry.activate(20260915301)

    assert registry.active_run_ids == frozenset({20260915101, 20260915301})
    assert registry.abort(20260915101, "user_requested") is True
    assert rendering.reason == "user_requested"
    assert watching.reason is None

    registry.clear(watching)
    assert registry.active_run_ids == frozenset({20260915101})
    assert registry.abort(20260915301, "too late") is False
