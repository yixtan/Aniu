"""The rule that lets an evaluation run while an analysis holds the account."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.business.evaluations import (
    EvaluationResult,
    EvaluationService,
    EvaluationStatus,
    FindingCandidate,
)
from backend.business.fill_record import AttributedOrder, assemble_fill_record
from backend.infra.db.models import RunEvaluationModel, RunJobModel, StrategyRunModel
from backend.infra.integrations.evaluation_agent import render_fill_record
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
        self.asked = ""

    async def evaluate(
        self, run_id: int, *, operator_question: str = ""
    ) -> EvaluationResult:
        del run_id
        self.calls += 1
        self.asked = operator_question
        return self.result


class ExplodingEvaluator:
    async def evaluate(
        self, run_id: int, *, operator_question: str = ""
    ) -> EvaluationResult:
        del run_id, operator_question
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


@pytest.mark.asyncio
async def test_drafted_candidates_survive_a_round_trip(session) -> None:
    """They reach the page on a later poll, not on the reply that wrote them,
    so storing them is the whole point."""

    _run(session)
    await session.commit()

    service = EvaluationService(
        RunEvaluationRepository(session),
        StubEvaluator(
            EvaluationResult(
                questions="一、证伪条件是什么？",
                answers="这个数我手上没有。",
                candidates=(
                    FindingCandidate(
                        finding="五笔全在一条链上，会同一天一起成交。",
                        resolution_test="把敞口拆到不相关的主线上。",
                    ),
                ),
            )
        ),
    )
    requested = await service.request(RUN_ID)
    await service.execute(requested.evaluation_id)
    await session.commit()

    reloaded = await RunEvaluationRepository(session).get_by_id(
        requested.evaluation_id
    )

    assert reloaded is not None
    assert len(reloaded.candidates) == 1
    assert reloaded.candidates[0].resolution_test == "把敞口拆到不相关的主线上。"


@pytest.mark.asyncio
async def test_a_question_of_your_own_reaches_the_reviewer(session) -> None:
    """The button on its own lets the reviewer pick every angle. A person with
    a doubt of their own had nowhere to put it."""

    _run(session)
    await session.commit()

    evaluator = StubEvaluator()
    service = EvaluationService(RunEvaluationRepository(session), evaluator)
    requested = await service.request(
        RUN_ID, operator_question="今天行情已经变了，为什么仓位还保持 20%？"
    )
    await service.execute(requested.evaluation_id)
    await session.commit()

    assert evaluator.asked == "今天行情已经变了，为什么仓位还保持 20%？"
    reloaded = await RunEvaluationRepository(session).get_by_id(
        requested.evaluation_id
    )
    assert reloaded is not None
    # Kept, not passed through: a review that asked something unusual cannot
    # be read back later without the reason it did.
    assert reloaded.operator_question.startswith("今天行情已经变了")


@pytest.mark.asyncio
async def test_pressing_the_button_alone_asks_nothing_extra(session) -> None:
    _run(session)
    await session.commit()

    evaluator = StubEvaluator()
    service = EvaluationService(RunEvaluationRepository(session), evaluator)
    requested = await service.request(RUN_ID)
    await service.execute(requested.evaluation_id)
    await session.commit()

    assert evaluator.asked == ""
    reloaded = await RunEvaluationRepository(session).get_by_id(
        requested.evaluation_id
    )
    assert reloaded is not None
    assert reloaded.operator_question == ""


@pytest.mark.asyncio
async def test_the_lead_survives_a_round_trip(session) -> None:
    """It reaches the page on a later poll, like everything else here."""

    _run(session)
    await session.commit()

    service = EvaluationService(
        RunEvaluationRepository(session),
        StubEvaluator(
            EvaluationResult(
                questions="一、证伪条件是什么？",
                answers="这个数我手上没有。",
                digest="主要担心一件事：挂着的单子全成交会超过你定的上限。",
            )
        ),
    )
    requested = await service.request(RUN_ID)
    await service.execute(requested.evaluation_id)
    await session.commit()

    reloaded = await RunEvaluationRepository(session).get_by_id(
        requested.evaluation_id
    )

    assert reloaded is not None
    assert reloaded.digest.startswith("主要担心一件事")


@pytest.mark.asyncio
async def test_a_review_that_drafted_nothing_reads_as_empty(session) -> None:
    """Same as a review written before drafting existed: no drafts to offer."""

    _run(session)
    await session.commit()

    service = EvaluationService(RunEvaluationRepository(session), StubEvaluator())
    requested = await service.request(RUN_ID)
    await service.execute(requested.evaluation_id)
    await session.commit()

    reloaded = await RunEvaluationRepository(session).get_by_id(
        requested.evaluation_id
    )

    assert reloaded is not None
    assert reloaded.candidates == ()
    assert reloaded.digest == ""


def test_the_record_keeps_a_runs_orders_apart_from_the_days() -> None:
    """The rendering the reviewer actually reads, in two blocks.

    One merged table is what let a reviewer treat a day's ten orders as this
    run's five and demand the difference be explained. There was no
    difference; the rest belonged to later runs.
    """

    def order(order_id: str, run: int | None, status: str) -> AttributedOrder:
        return AttributedOrder(
            order_id=order_id,
            run_id=run,
            symbol="300394",
            stock_name="天孚通信",
            direction="BUY",
            quantity=200,
            order_price=265.0,
            status=status,
            submitted_at=datetime(2026, 9, 17, 1, 37, tzinfo=UTC),
        )

    rendered = render_fill_record(
        assemble_fill_record(
            RUN_ID,
            [
                order("a", RUN_ID, "PENDING"),
                order("b", RUN_ID, "CANCELLED"),
                order("c", 20260917102, "FILLED"),
                order("d", None, "REJECTED"),
            ],
        )
    )

    assert f"## 本次运行（{RUN_ID}）自己下的委托" in rendered
    assert "本次共 2 笔，成交 0 笔" in rendered
    # The day counts all four and says how many runs placed them.
    assert "| 2026-09-17 | 2 | 4 | 1 | 25% |" in rendered
    assert "其中 1 笔早期委托归属不明" in rendered
