"""Per-day run counts, as the timetable's date navigation asks for them."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.business.shared.enums import RunStatus
from backend.infra.db.models import StrategyRunModel
from backend.infra.repositories import RunRepository


async def _add_run(
    session,
    run_id: int,
    *,
    status: RunStatus = RunStatus.COMPLETED,
) -> None:
    session.add(
        StrategyRunModel(
            id=run_id,
            trigger_source="SCHEDULED",
            status=status.value,
            current_state="Summary",
            snapshot_json={},
            trace_json={},
            started_at=datetime.now(tz=UTC).isoformat(),
            completed_at=datetime.now(tz=UTC).isoformat(),
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_the_two_tasks_are_counted_apart_newest_day_first(session) -> None:
    """A watch outnumbers an analysis about five to one, so one combined total
    would say almost nothing about whether the analyses ran."""

    await _add_run(session, 20260911101)
    await _add_run(session, 20260914101)
    await _add_run(session, 20260914102)
    await _add_run(session, 20260914301)
    await _add_run(session, 20260914302)
    await _add_run(session, 20260914303)

    days = await RunRepository(session).list_run_days()

    assert [day["day"] for day in days] == ["20260914", "20260911"]
    assert days[0]["analysis_total"] == 2
    assert days[0]["watch_total"] == 3
    assert days[1]["analysis_total"] == 1
    assert days[1]["watch_total"] == 0


@pytest.mark.asyncio
async def test_a_failure_is_counted_in_its_own_task_and_still_in_the_total(
    session,
) -> None:
    await _add_run(session, 20260914101)
    await _add_run(session, 20260914102, status=RunStatus.FAILED)
    await _add_run(session, 20260914301, status=RunStatus.FAILED)

    days = await RunRepository(session).list_run_days()

    assert days[0]["analysis_total"] == 2
    assert days[0]["analysis_failed"] == 1
    assert days[0]["watch_total"] == 1
    assert days[0]["watch_failed"] == 1


@pytest.mark.asyncio
async def test_an_aborted_run_counts_as_gone_wrong(session) -> None:
    """The timetable reports on which slots produced a report; a run someone
    stopped produced none, same as one that failed."""

    await _add_run(session, 20260914101, status=RunStatus.ABORTED)

    days = await RunRepository(session).list_run_days()

    assert days[0]["analysis_failed"] == 1


@pytest.mark.asyncio
async def test_a_running_run_is_counted_but_not_as_a_failure(session) -> None:
    await _add_run(session, 20260914101, status=RunStatus.RUNNING)

    days = await RunRepository(session).list_run_days()

    assert days[0]["analysis_total"] == 1
    assert days[0]["analysis_failed"] == 0


@pytest.mark.asyncio
async def test_the_limit_caps_how_many_days_come_back(session) -> None:
    await _add_run(session, 20260912101)
    await _add_run(session, 20260913101)
    await _add_run(session, 20260914101)

    days = await RunRepository(session).list_run_days(limit=2)

    assert [day["day"] for day in days] == ["20260914", "20260913"]
