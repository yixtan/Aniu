"""API tests for run snapshot endpoints."""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_run_detail_endpoint_returns_trace_snapshot(
    run_api_client: AsyncClient,
) -> None:
    started = await run_api_client.post("/api/aniu/runs/start", json={})
    run_id = started.json()["run_id"]

    detail = None
    for _ in range(50):
        detail = await run_api_client.get(f"/api/aniu/runs/{run_id}")
        assert detail.status_code == 200
        if detail.json()["trace"]["stages"] and detail.json()["status"] != "RUNNING":
            break
        await asyncio.sleep(0.05)
    runs = await run_api_client.get("/api/aniu/runs")

    assert started.status_code == 201
    assert detail is not None
    assert detail.status_code == 200
    assert runs.status_code == 200
    assert detail.json()["trace"]["stages"]
    assert detail.json()["summary_render_mode"] in {"markdown", "html"}
    summary = runs.json()[0]
    assert summary["run_id"] == run_id
    assert isinstance(summary["tool_calls_count"], int)
    assert isinstance(summary["thinking_count"], int)
    assert isinstance(summary["total_tokens"], int)
    assert isinstance(summary["trade_count"], int)
    assert summary["summary_render_mode"] in {"markdown", "html"}
    assert "trade_stage_status" not in summary


@pytest.mark.asyncio
async def test_run_detail_endpoint_returns_not_found(
    run_api_client: AsyncClient,
) -> None:
    detail = await run_api_client.get("/api/aniu/runs/999")

    assert detail.status_code == 404


def test_the_trace_contract_names_every_stage_the_domain_can_emit() -> None:
    """A stage key the schema omits makes that run unfetchable, not unnamed.

    `response_model` validates on the way out, so a watch run — whose trace
    carries the key `watch` — came back as a 500 while this literal still said
    only run and summary. The frontend could not have caught it either: the
    generated types are built from this same contract.
    """

    from typing import get_args

    from backend.api.schemas.run import TraceStageKey
    from backend.business.runs.pipeline_stages import TRACE_STAGE_META

    assert set(get_args(TraceStageKey)) == set(TRACE_STAGE_META)


def test_a_watch_stage_survives_the_response_model() -> None:
    """The end the bug was actually felt at: serialising a watch's trace."""

    from backend.api.schemas.run import TraceStageResponse
    from backend.business.runs.pipeline_stages import WATCH

    stage = TraceStageResponse.model_validate(
        {
            "stage_id": WATCH.stage_id,
            "key": WATCH.trace_key,
            "status": "completed",
            "started_at": None,
            "ended_at": None,
            "steps": [],
        }
    )

    assert stage.key == "watch"
@pytest.mark.asyncio
async def test_run_days_is_not_swallowed_by_the_run_id_route(
    api_client: AsyncClient,
) -> None:
    """`/runs/days` sits next to `/runs/{run_id}`, which parses an int.

    Declared the other way round, "days" would be read as a run id and refused
    as a malformed integer, so this pins the declaration order rather than the
    payload.
    """

    response = await api_client.get("/api/aniu/runs/days")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_a_finished_run_shows_up_in_its_day(
    run_api_client: AsyncClient,
) -> None:
    started = await run_api_client.post("/api/aniu/runs/start", json={})
    assert started.status_code == 201

    response = await run_api_client.get("/api/aniu/runs/days")

    assert response.status_code == 200
    days = response.json()
    assert len(days) == 1
    assert days[0]["analysis_total"] == 1
    # The analysis columns mean analyses only; nothing leaks across.
    assert days[0]["watch_total"] == 0
