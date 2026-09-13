"""Wiring the order watch to a schedule: dispatch, waiting, staleness."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from backend.bootstrap import schedule_handlers
from backend.bootstrap.schedule_handlers import (
    build_market_analysis_handler,
    build_order_watch_handler,
)
from backend.business.runs import StrategyRun, StrategySnapshot
from backend.business.runs.abort_registry import ActiveRunAbortRegistry
from backend.business.runs.job import RunJobStatus
from backend.business.schedules import StrategySchedule
from backend.business.shared import ConcurrentRunError
from backend.business.shared.enums import RunStatus, TriggerSource
from backend.infra.repositories import RunRepository
from backend.infra.repositories.run_job_repo import RunJobRepository
from backend.infra.workers.run_worker import WATCH_STALE_AFTER, RunWorker

WATCH_RUN_ID = 20260913301
ANALYSIS_RUN_ID = 20260913101


async def _no_lease_check() -> None:
    return None


@dataclass
class Created:
    run_id: int


@dataclass
class ScriptedRunService:
    """Refuses the first N calls with the given active run, then succeeds."""

    refuse_times: int
    active_run_id: int
    calls: int = 0
    created: Created = field(default_factory=lambda: Created(run_id=1))

    async def create_run(self, command, *, execution_guard=None):
        del command, execution_guard
        return await self._attempt()

    async def create_watch_run(self, command, *, execution_guard=None):
        del command, execution_guard
        return await self._attempt()

    async def _attempt(self):
        self.calls += 1
        if self.calls <= self.refuse_times:
            raise ConcurrentRunError(self.active_run_id)
        return self.created


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def _schedule(task_type: str) -> StrategySchedule:
    return StrategySchedule(
        schedule_id=1, enabled=True, interval_minutes=15, task_type=task_type
    )


@pytest.mark.asyncio
async def test_an_analysis_waits_out_a_watch_instead_of_losing_its_slot(
    monkeypatch,
) -> None:
    """A three-second watch must not cost a twenty-minute analysis slot."""

    monkeypatch.setattr(schedule_handlers, "_WAIT_POLL_SECONDS", 0.0)
    service = ScriptedRunService(refuse_times=2, active_run_id=WATCH_RUN_ID)
    enqueued: list[int] = []

    async def enqueue(run_id: int) -> None:
        enqueued.append(run_id)

    handler = build_market_analysis_handler(
        session_factory=_Session,  # type: ignore[arg-type]
        run_service_factory=lambda _s: service,  # type: ignore[arg-type,return-value]
        enqueue_run=enqueue,
    )

    await handler(_schedule("market_analysis"), _no_lease_check)

    assert service.calls == 3
    assert enqueued == [1]


@pytest.mark.asyncio
async def test_an_analysis_does_not_wait_for_another_analysis(monkeypatch) -> None:
    """That slot is genuinely taken; waiting would only stack a second run."""

    monkeypatch.setattr(schedule_handlers, "_WAIT_POLL_SECONDS", 0.0)
    service = ScriptedRunService(refuse_times=1, active_run_id=ANALYSIS_RUN_ID)
    handler = build_market_analysis_handler(
        session_factory=_Session,  # type: ignore[arg-type]
        run_service_factory=lambda _s: service,  # type: ignore[arg-type,return-value]
    )

    with pytest.raises(ConcurrentRunError):
        await handler(_schedule("market_analysis"), _no_lease_check)

    assert service.calls == 1


@pytest.mark.asyncio
async def test_an_analysis_gives_up_on_a_watch_that_never_ends(monkeypatch) -> None:
    monkeypatch.setattr(schedule_handlers, "_WAIT_POLL_SECONDS", 0.0)
    monkeypatch.setattr(schedule_handlers, "ANALYSIS_WAITS_FOR_WATCH_SECONDS", 0.0)
    service = ScriptedRunService(refuse_times=99, active_run_id=WATCH_RUN_ID)
    handler = build_market_analysis_handler(
        session_factory=_Session,  # type: ignore[arg-type]
        run_service_factory=lambda _s: service,  # type: ignore[arg-type,return-value]
    )

    with pytest.raises(ConcurrentRunError):
        await handler(_schedule("market_analysis"), _no_lease_check)


@pytest.mark.asyncio
async def test_a_watch_that_finds_a_run_in_flight_simply_does_not_happen() -> None:
    """The next pass is minutes away and reads the same plan."""

    service = ScriptedRunService(refuse_times=1, active_run_id=ANALYSIS_RUN_ID)
    enqueued: list[int] = []

    async def enqueue(run_id: int) -> None:
        enqueued.append(run_id)

    handler = build_order_watch_handler(
        session_factory=_Session,  # type: ignore[arg-type]
        run_service_factory=lambda _s: service,  # type: ignore[arg-type,return-value]
        enqueue_run=enqueue,
    )

    await handler(_schedule("order_watch"), _no_lease_check)

    assert service.calls == 1
    assert enqueued == []


async def _pending_run(session_factory, run_id: int, *, age: timedelta) -> None:
    async with session_factory() as session:
        await RunRepository(session).add(
            StrategyRun(
                run_id=run_id,
                trigger_source=TriggerSource.SCHEDULED,
                schedule_id=1,
                snapshot=StrategySnapshot(
                    prompt_version="v1", risk_rules_version="risk-v1"
                ),
            )
        )
        await RunJobRepository(session).create_pending(run_id)
        await session.commit()
        created_at = (datetime.now(tz=UTC) - age).isoformat()
        await session.execute(
            text("UPDATE run_jobs SET created_at = :at WHERE run_id = :run_id"),
            {"at": created_at, "run_id": run_id},
        )
        await session.commit()


def _worker(session_factory, *, executor_called: list[int]) -> RunWorker:
    def executor(_session):
        executor_called.append(1)
        raise AssertionError("must not execute")

    return RunWorker(
        session_factory=session_factory,
        executor_factory=executor,  # type: ignore[arg-type]
        abort_registry=ActiveRunAbortRegistry(),
        worker_id="test-worker",
    )


@pytest.mark.asyncio
async def test_a_watch_claimed_a_cadence_late_is_dropped(session_factory) -> None:
    """It sat behind a long analysis; the next pass is due and reads the same plan."""

    await _pending_run(
        session_factory, WATCH_RUN_ID, age=WATCH_STALE_AFTER + timedelta(seconds=1)
    )
    called: list[int] = []

    worker = _worker(session_factory, executor_called=called)
    assert await worker._claim_and_execute_one()

    assert called == []
    async with session_factory() as session:
        run = await RunRepository(session).get_by_id(WATCH_RUN_ID)
        job = await RunJobRepository(session).get_by_run_id(WATCH_RUN_ID)
    assert run is not None and run.status is RunStatus.FAILED
    assert job is not None and job.status is RunJobStatus.INTERRUPTED
    assert job.last_error_code == "stale_order_watch"


@pytest.mark.asyncio
async def test_an_analysis_is_never_dropped_for_age(session_factory) -> None:
    """Staleness is a watch's problem: an analysis slot is not overtaken."""

    await _pending_run(
        session_factory, ANALYSIS_RUN_ID, age=WATCH_STALE_AFTER + timedelta(hours=1)
    )
    called: list[int] = []

    # The executor double raises, so the worker reports the run failed — the
    # point is that it *tried*, which the stale path never does.
    await _worker(session_factory, executor_called=called)._claim_and_execute_one()

    assert called == [1]
