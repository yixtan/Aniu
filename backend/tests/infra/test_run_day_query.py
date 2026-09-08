"""Which market days hold a completed run, as the dream scheduler asks it."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from backend.business.shared.enums import RunStatus
from backend.infra.db.models import StrategyRunModel
from backend.infra.repositories import RunRepository

_SHANGHAI = ZoneInfo("Asia/Shanghai")


async def _add_run(
    session,
    run_id: int,
    *,
    completed_at: datetime | None,
    status: RunStatus = RunStatus.COMPLETED,
) -> None:
    session.add(
        StrategyRunModel(
            id=run_id,
            trigger_source="MANUAL",
            status=status.value,
            current_state="Summary",
            snapshot_json={},
            trace_json={},
            started_at=(completed_at or datetime.now(tz=UTC)).isoformat(),
            completed_at=None if completed_at is None else completed_at.isoformat(),
        )
    )
    await session.flush()


def _shanghai(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_SHANGHAI).astimezone(UTC)


@pytest.mark.asyncio
async def test_days_come_back_newest_first_without_repeats(session) -> None:
    await _add_run(session, 1, completed_at=_shanghai(2026, 9, 7, 10))
    await _add_run(session, 2, completed_at=_shanghai(2026, 9, 7, 14))
    await _add_run(session, 3, completed_at=_shanghai(2026, 9, 8, 9))

    days = await RunRepository(session).recent_days_with_runs(limit=5)

    assert days == [date(2026, 9, 8), date(2026, 9, 7)]


@pytest.mark.asyncio
async def test_a_day_is_the_shanghai_day_not_the_utc_one(session) -> None:
    """A run finishing at 22:00 Shanghai is still that day's, not tomorrow's."""

    await _add_run(session, 1, completed_at=_shanghai(2026, 9, 8, 22))

    days = await RunRepository(session).recent_days_with_runs(limit=5)

    # 22:00 Shanghai is 14:00 UTC the same day; 00:30 Shanghai would be the
    # previous UTC day, and reading either in UTC picks the wrong date.
    assert days == [date(2026, 9, 8)]


@pytest.mark.asyncio
async def test_a_run_finishing_after_midnight_belongs_to_the_new_day(session) -> None:
    await _add_run(session, 1, completed_at=_shanghai(2026, 9, 8, 23, 50))
    await _add_run(session, 2, completed_at=_shanghai(2026, 9, 9, 0, 10))

    days = await RunRepository(session).recent_days_with_runs(limit=5)

    assert days == [date(2026, 9, 9), date(2026, 9, 8)]


@pytest.mark.asyncio
async def test_only_completed_runs_count(session) -> None:
    """A failed or running day has no report for the dream to read."""

    await _add_run(session, 1, completed_at=_shanghai(2026, 9, 8, 10))
    await _add_run(
        session,
        2,
        completed_at=_shanghai(2026, 9, 7, 10),
        status=RunStatus.FAILED,
    )
    await _add_run(session, 3, completed_at=None, status=RunStatus.RUNNING)

    days = await RunRepository(session).recent_days_with_runs(limit=5)

    assert days == [date(2026, 9, 8)]


@pytest.mark.asyncio
async def test_the_limit_counts_days_not_runs(session) -> None:
    """Many runs on one day must not use up the whole lookback."""

    for index, hour in enumerate((9, 10, 11, 13, 14), start=1):
        await _add_run(session, index, completed_at=_shanghai(2026, 9, 8, hour))
    await _add_run(session, 90, completed_at=_shanghai(2026, 9, 7, 10))
    await _add_run(session, 91, completed_at=_shanghai(2026, 9, 4, 10))
    await _add_run(session, 92, completed_at=_shanghai(2026, 9, 3, 10))

    days = await RunRepository(session).recent_days_with_runs(limit=3)

    assert days == [date(2026, 9, 8), date(2026, 9, 7), date(2026, 9, 4)]


@pytest.mark.asyncio
async def test_no_runs_at_all_is_an_empty_answer(session) -> None:
    assert await RunRepository(session).recent_days_with_runs(limit=3) == []
