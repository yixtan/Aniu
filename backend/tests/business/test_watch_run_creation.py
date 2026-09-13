"""Creating an order-watch run."""

from __future__ import annotations

from dataclasses import replace

import pytest

from backend.business.runs import ORDER_WATCH_TASK_TYPE
from backend.business.runs.abort_registry import ActiveRunAbortRegistry
from backend.business.runs.commands import StartRunCommand, StartWatchCommand
from backend.business.runs.numbering import is_order_watch_task, task_type_of
from backend.business.runs.service import RunService
from backend.business.shared import ConcurrentRunError, ServiceConfigurationError
from backend.business.shared.enums import RunState, TriggerSource
from backend.tests.business.test_run_app_service import (
    FIXED_NOW,
    InMemoryRunRepository,
    NullRunJobRepo,
    make_configured_model_repositories,
)


def _service(run_repo: InMemoryRunRepository | None = None) -> RunService:
    settings_repo, profile_repo, selected_repo = make_configured_model_repositories()
    return RunService(
        run_repo=run_repo or InMemoryRunRepository(),
        settings_repo=settings_repo,
        model_profile_repo=profile_repo,
        selected_model_repo=selected_repo,
        run_job_repo=NullRunJobRepo(),
        abort_registry=ActiveRunAbortRegistry(),
        now_provider=lambda: FIXED_NOW,
    )


@pytest.mark.asyncio
async def test_a_watch_run_is_numbered_apart_and_starts_at_its_own_stage() -> None:
    created = await _service().create_watch_run(StartWatchCommand(schedule_id=7))

    assert task_type_of(created.run_id) == ORDER_WATCH_TASK_TYPE
    assert is_order_watch_task(created.run_id)
    assert created.current_state == RunState.WATCH.value
    assert created.trigger_source == TriggerSource.SCHEDULED.value
    assert created.schedule_id == 7


@pytest.mark.asyncio
async def test_a_watch_freezes_only_its_own_stage_model() -> None:
    """So the runtime builder makes one runtime, for Watch, and no others."""

    run_repo = InMemoryRunRepository()
    created = await _service(run_repo).create_watch_run(
        StartWatchCommand(schedule_id=7)
    )

    stored = run_repo.runs[created.run_id]
    assert set(stored.snapshot.stage_models) == {"Watch"}


@pytest.mark.asyncio
async def test_a_watch_is_refused_while_any_run_is_active() -> None:
    run_repo = InMemoryRunRepository()
    service = _service(run_repo)
    analysis = await service.create_run(StartRunCommand())

    with pytest.raises(ConcurrentRunError) as caught:
        await service.create_watch_run(StartWatchCommand(schedule_id=7))

    assert caught.value.running_run_id == analysis.run_id


@pytest.mark.asyncio
async def test_an_unconfigured_watch_model_fails_loudly_not_expensively() -> None:
    """It does not borrow Run's model the way Dream does.

    A watch runs 82 times a day; quietly borrowing a heavy analysis model would
    cost ten times what it should and look like nothing was wrong.
    """

    settings_repo, profile_repo, selected_repo = make_configured_model_repositories()
    settings = settings_repo.settings
    settings.stage_settings["Watch"] = replace(
        settings.stage_settings["Watch"], model_selected_model_id=None
    )
    service = RunService(
        run_repo=InMemoryRunRepository(),
        settings_repo=settings_repo,
        model_profile_repo=profile_repo,
        selected_model_repo=selected_repo,
        run_job_repo=NullRunJobRepo(),
        abort_registry=ActiveRunAbortRegistry(),
        now_provider=lambda: FIXED_NOW,
    )

    with pytest.raises(ServiceConfigurationError, match="Watch"):
        await service.create_watch_run(StartWatchCommand(schedule_id=7))
