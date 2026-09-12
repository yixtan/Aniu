"""What the order watch stage is, and what it is allowed to touch."""

from __future__ import annotations

from backend.business.dreams.models import DREAM_TASK_TYPE
from backend.business.runs.numbering import (
    ORDER_WATCH_TASK_TYPE,
    RUN_TASK_TYPE,
    SCHEDULE_TASK_TYPE,
)
from backend.business.runs.pipeline_stages import (
    PIPELINE,
    WATCH,
    is_tool_capable_stage,
    is_write_stage,
    pipeline_stage_for_state_name,
)
from backend.business.runs.state_machine_rules import (
    WATCH_INITIAL_STATE,
    can_transition,
)
from backend.business.settings.prompt import AniuAgentPrompt
from backend.business.settings.stages import STAGE_IDS, default_stage_settings
from backend.business.shared.enums import RunState


def test_the_watch_is_not_a_step_of_the_analysis_run() -> None:
    """It is its own run, so it must not appear in the two-stage FSM."""

    assert WATCH not in PIPELINE
    assert tuple(stage.stage_id for stage in PIPELINE) == ("Run", "Summary")
    assert pipeline_stage_for_state_name("Watch") is WATCH


def test_a_watch_run_is_one_stage_and_renders_nothing() -> None:
    assert WATCH_INITIAL_STATE is RunState.WATCH
    assert can_transition(RunState.WATCH, RunState.COMPLETED)
    # Going through Summary would put an empty report in the run list 82 times
    # a day; a watch says what it did in its own stage.
    assert not can_transition(RunState.WATCH, RunState.SUMMARY)


def test_the_watch_may_call_tools_and_may_cause_side_effects() -> None:
    """Cancelling is a side effect, so the stage has to be a write stage."""

    assert is_tool_capable_stage("Watch")
    assert is_write_stage("Watch")


def test_the_watch_is_numbered_apart_from_every_other_task() -> None:
    """The ninth digit of a task id says who acted; it has to stay unique."""

    digits = {RUN_TASK_TYPE, SCHEDULE_TASK_TYPE, ORDER_WATCH_TASK_TYPE, DREAM_TASK_TYPE}
    assert len(digits) == 4
    assert 0 <= ORDER_WATCH_TASK_TYPE <= 9


def test_the_watch_is_configured_like_any_other_stage() -> None:
    assert "Watch" in STAGE_IDS
    settings = default_stage_settings(AniuAgentPrompt())
    assert settings["Watch"].prompt
    # Deliberately not inherited from Run the way Dream's model is: a watch
    # runs 82 times a day, and silently borrowing a heavy analysis model would
    # cost ten times what it should. Unconfigured must fail, not overspend.
    assert settings["Watch"].model_selected_model_id is None
