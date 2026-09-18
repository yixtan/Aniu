"""Requesting and reading an independent review of a finished run."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from backend.infra.db.models import RunEvaluationModel, StrategyRunModel

RUN_ID = 20260917101


def _finished_run(session) -> None:
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
async def test_requesting_a_review_queues_it_and_returns_at_once(
    api_client: AsyncClient, session
) -> None:
    """The button must answer immediately even while the account is busy.

    The review runs on its own lane, so the request never waits on a run and
    never competes for the exclusive guard. What comes back is the pending
    row, not the finished review.
    """

    _finished_run(session)
    await session.commit()

    response = await api_client.post(f"/api/aniu/runs/{RUN_ID}/evaluation")

    assert response.status_code == 201
    body = response.json()
    assert body["run_id"] == RUN_ID
    assert body["status"] == "PENDING"
    assert body["questions"] is None
    rows = (await session.execute(RunEvaluationModel.__table__.select())).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_a_run_with_no_review_says_so(api_client: AsyncClient, session) -> None:
    _finished_run(session)
    await session.commit()

    response = await api_client.get(f"/api/aniu/runs/{RUN_ID}/evaluation")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reading_a_review_returns_the_latest_one(
    api_client: AsyncClient, session
) -> None:
    """Two presses leave two rows; the page shows the newer."""

    _finished_run(session)
    session.add_all(
        [
            RunEvaluationModel(
                run_id=RUN_ID,
                status="COMPLETED",
                questions="旧的提问",
                answers="旧的回答",
                total_tokens=100,
                cached_tokens=40,
                created_at="2026-09-17T02:00:00+00:00",
            ),
            RunEvaluationModel(
                run_id=RUN_ID,
                status="COMPLETED",
                questions="新的提问",
                answers="新的回答",
                total_tokens=200,
                cached_tokens=80,
                created_at="2026-09-17T03:00:00+00:00",
            ),
        ]
    )
    await session.commit()

    body = (await api_client.get(f"/api/aniu/runs/{RUN_ID}/evaluation")).json()

    assert body["questions"] == "新的提问"
    assert body["total_tokens"] == 200
    assert body["cached_tokens"] == 80


@pytest.mark.asyncio
async def test_drafted_candidates_reach_the_page(
    api_client: AsyncClient, session
) -> None:
    """The page fills its form from these, so they have to be on the contract
    and not merely in the database."""

    _finished_run(session)
    session.add(
        RunEvaluationModel(
            run_id=RUN_ID,
            status="COMPLETED",
            questions="一、证伪条件是什么？",
            answers="这个数我手上没有。",
            digest="主要担心挂单全成交会超过上限，第 3 问最要紧。",
            candidates_json=(
                '[{"finding": "五笔全在一条链上。", '
                '"resolution_test": "把敞口拆到不相关的主线上。"}]'
            ),
            created_at="2026-09-18T03:00:00+00:00",
        )
    )
    await session.commit()

    body = (await api_client.get(f"/api/aniu/runs/{RUN_ID}/evaluation")).json()

    assert body["candidates"] == [
        {
            "finding": "五笔全在一条链上。",
            "resolution_test": "把敞口拆到不相关的主线上。",
        }
    ]
    # The lead is what the page shows first, so it has to be on the contract.
    assert body["digest"].startswith("主要担心挂单")


@pytest.mark.asyncio
async def test_a_review_with_no_drafts_reads_as_an_empty_list(
    api_client: AsyncClient, session
) -> None:
    """Never null: the page maps over this without a guard."""

    _finished_run(session)
    session.add(
        RunEvaluationModel(
            run_id=RUN_ID,
            status="COMPLETED",
            questions="一、证伪条件是什么？",
            answers="这个数我手上没有。",
            created_at="2026-09-18T03:00:00+00:00",
        )
    )
    await session.commit()

    body = (await api_client.get(f"/api/aniu/runs/{RUN_ID}/evaluation")).json()

    assert body["candidates"] == []
    assert body["digest"] == ""
