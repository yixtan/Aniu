"""The rule that lets an evaluation run while an analysis holds the account."""

from __future__ import annotations

import pytest

from backend.business.evaluations import (
    EvaluationResult,
    EvaluationService,
    EvaluationStatus,
)
from backend.infra.db.models import RunEvaluationModel, RunJobModel, StrategyRunModel
from backend.infra.repositories.run_evaluation_repo import RunEvaluationRepository
from backend.infra.repositories.run_job_repo import RunJobRepository

RUN_ID = 20260917101


class StubEvaluator:
    def __init__(self, result: EvaluationResult | None = None) -> None:
        self.result = result or EvaluationResult(
            questions="一、你的证伪条件是什么？",
            answers="我手上没有这个数，需要调用组合查询。",
            total_tokens=2_000,
            cached_tokens=500,
        )
        self.calls = 0

    async def evaluate(self, run_id: int) -> EvaluationResult:
        del run_id
        self.calls += 1
        return self.result


class ExplodingEvaluator:
    async def evaluate(self, run_id: int) -> EvaluationResult:
        del run_id
        raise RuntimeError("provider refused the request")


def _run(session) -> None:
    session.add(
        StrategyRunModel(
            id=RUN_ID,
            trigger_source="SCHEDULED",
            status="COMPLETED",
            current_state="Completed",
            snapshot_json={},
            trace_json={},
            summary_render_mode="html",
            started_at="2026-09-17T01:30:00+00:00",
            completed_at="2026-09-17T01:40:00+00:00",
        )
    )


@pytest.mark.asyncio
async def test_an_evaluation_runs_while_another_run_holds_the_account(session) -> None:
    """The whole reason the lane exists.

    `run_jobs` takes `active_guard` when a row is created, not when it is
    claimed, and the guard is unique — so anything submitted there while an
    analysis is active is refused outright, not queued. An evaluation reaches
    no tool that can trade, so it has no business waiting for the account.
    """

    _run(session)
    session.add(
        StrategyRunModel(
            id=20260917102,
            trigger_source="SCHEDULED",
            status="RUNNING",
            current_state="Run",
            snapshot_json={},
            trace_json={},
            summary_render_mode="markdown",
            started_at="2026-09-17T01:50:00+00:00",
        )
    )
    await session.flush()
    # An analysis is mid-flight and holds the exclusive guard.
    await RunJobRepository(session).create_pending(20260917102)
    await session.commit()

    evaluator = StubEvaluator()
    service = EvaluationService(RunEvaluationRepository(session), evaluator)
    requested = await service.request(RUN_ID)
    finished = await service.execute(requested.evaluation_id)

    assert evaluator.calls == 1
    assert finished is not None
    assert finished.status is EvaluationStatus.COMPLETED
    assert finished.questions == "一、你的证伪条件是什么？"
    # And the guard is still held by the analysis, untouched.
    active = await RunJobRepository(session).get_active_job()
    assert active is not None
    assert active.run_id == 20260917102


@pytest.mark.asyncio
async def test_an_evaluation_never_becomes_a_run_job(session) -> None:
    """Nothing about a review belongs in the queue that serializes trading."""

    _run(session)
    await session.commit()

    service = EvaluationService(RunEvaluationRepository(session), StubEvaluator())
    requested = await service.request(RUN_ID)
    await service.execute(requested.evaluation_id)

    jobs = (await session.execute(RunJobModel.__table__.select())).all()
    assert jobs == []
    rows = (await session.execute(RunEvaluationModel.__table__.select())).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_a_failing_evaluator_is_recorded_not_raised(session) -> None:
    """A review that could not be produced is a failed review, not an error
    the caller has to catch — nothing downstream of it is waiting."""

    _run(session)
    await session.commit()

    service = EvaluationService(RunEvaluationRepository(session), ExplodingEvaluator())
    requested = await service.request(RUN_ID)
    finished = await service.execute(requested.evaluation_id)

    assert finished is not None
    assert finished.status is EvaluationStatus.FAILED
    assert finished.failure_reason == "provider refused the request"
    assert finished.completed_at is not None


@pytest.mark.asyncio
async def test_a_second_execute_does_nothing(session) -> None:
    """Two presses of one button must not spend the tokens twice."""

    _run(session)
    await session.commit()

    evaluator = StubEvaluator()
    service = EvaluationService(RunEvaluationRepository(session), evaluator)
    requested = await service.request(RUN_ID)

    await service.execute(requested.evaluation_id)
    again = await service.execute(requested.evaluation_id)

    assert evaluator.calls == 1
    assert again is not None
    assert again.status is EvaluationStatus.COMPLETED


@pytest.mark.asyncio
async def test_the_cached_share_can_never_exceed_the_total(session) -> None:
    _run(session)
    await session.commit()

    service = EvaluationService(
        RunEvaluationRepository(session),
        StubEvaluator(
            EvaluationResult(
                questions="q",
                answers="a",
                total_tokens=100,
                cached_tokens=9_999,
            )
        ),
    )
    requested = await service.request(RUN_ID)
    finished = await service.execute(requested.evaluation_id)

    assert finished is not None
    assert finished.cached_tokens == 100
