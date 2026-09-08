"""Choosing which days still need a dream, and getting them runnable."""

from __future__ import annotations

from datetime import date

import pytest

from backend.business.dreams import DreamStatus
from backend.business.dreams.service import DreamService
from backend.infra.repositories.memory_dream_repo import MemoryDreamRepository


class FakeRunDays:
    """Stands in for the run history the dream reflects on."""

    def __init__(self, days: list[date]) -> None:
        self.days = days
        self.asked_for: list[int] = []

    async def recent_days_with_runs(self, *, limit: int) -> list[date]:
        self.asked_for.append(limit)
        return self.days[:limit]


def _service(session, days: list[date]) -> tuple[DreamService, FakeRunDays]:
    run_days = FakeRunDays(days)
    service = DreamService(
        MemoryDreamRepository(session),
        committer=session,
        run_days=run_days,
    )
    return service, run_days


async def _dream_in_status(session, target: date, status: DreamStatus) -> None:
    service, _ = _service(session, [])
    dream = await service.create_or_get(target)
    if status is DreamStatus.PENDING:
        return
    dream.start()
    if status is DreamStatus.COMPLETED:
        dream.complete("整理完毕")
    elif status is DreamStatus.FAILED:
        dream.fail("模型超时")
    await MemoryDreamRepository(session).save(dream)
    await session.commit()


@pytest.mark.asyncio
async def test_nothing_pending_when_every_run_day_is_covered(session) -> None:
    days = [date(2026, 9, 8), date(2026, 9, 7)]
    for day in days:
        await _dream_in_status(session, day, DreamStatus.COMPLETED)
    service, _ = _service(session, days)

    assert await service.pending_target_dates() == []


@pytest.mark.asyncio
async def test_the_newest_uncovered_day_comes_first(session) -> None:
    days = [date(2026, 9, 8), date(2026, 9, 7), date(2026, 9, 4)]
    await _dream_in_status(session, date(2026, 9, 4), DreamStatus.COMPLETED)
    service, _ = _service(session, days)

    assert await service.pending_target_dates() == [
        date(2026, 9, 8),
        date(2026, 9, 7),
    ]


@pytest.mark.asyncio
async def test_a_gap_between_covered_days_is_found(session) -> None:
    """The case the old clock-based target could never come back to."""

    days = [date(2026, 9, 8), date(2026, 9, 7), date(2026, 9, 4)]
    await _dream_in_status(session, date(2026, 9, 8), DreamStatus.COMPLETED)
    await _dream_in_status(session, date(2026, 9, 4), DreamStatus.COMPLETED)
    service, _ = _service(session, days)

    assert await service.pending_target_dates() == [date(2026, 9, 7)]


@pytest.mark.asyncio
async def test_a_day_without_runs_is_never_pending(session) -> None:
    """An empty day has no report; dreaming it spends a turn to learn that."""

    service, run_days = _service(session, [])

    assert await service.pending_target_dates() == []
    assert run_days.asked_for == [3]


@pytest.mark.asyncio
async def test_a_failed_dream_leaves_its_day_pending(session) -> None:
    await _dream_in_status(session, date(2026, 9, 8), DreamStatus.FAILED)
    service, _ = _service(session, [date(2026, 9, 8)])

    assert await service.pending_target_dates() == [date(2026, 9, 8)]


@pytest.mark.asyncio
async def test_a_running_dream_leaves_its_day_pending(session) -> None:
    """Its day is not reflected on yet; the guard against a second start is
    the status check when the task is prepared, not this list."""

    await _dream_in_status(session, date(2026, 9, 8), DreamStatus.RUNNING)
    service, _ = _service(session, [date(2026, 9, 8)])

    assert await service.pending_target_dates() == [date(2026, 9, 8)]


@pytest.mark.asyncio
async def test_the_window_is_counted_in_run_days(session) -> None:
    days = [date(2026, 9, 8), date(2026, 9, 7), date(2026, 9, 4), date(2026, 9, 3)]
    service, run_days = _service(session, days)

    pending = await service.pending_target_dates()

    assert pending == days[:3]
    assert date(2026, 9, 3) not in pending
    assert run_days.asked_for == [3]


@pytest.mark.asyncio
async def test_preparing_a_failed_day_puts_it_back_to_pending(session) -> None:
    """Eligibility asks for a completed dream, so a failure left alone would
    be selected every night and enqueued none of them."""

    await _dream_in_status(session, date(2026, 9, 8), DreamStatus.FAILED)
    service, _ = _service(session, [date(2026, 9, 8)])

    dream = await service.prepare_scheduled_run(date(2026, 9, 8))

    assert dream.status is DreamStatus.PENDING
    assert dream.failure_reason is None
    stored = await MemoryDreamRepository(session).get_by_date(date(2026, 9, 8))
    assert stored is not None
    assert stored.status is DreamStatus.PENDING


@pytest.mark.asyncio
async def test_preparing_a_running_day_does_not_restart_it(session) -> None:
    await _dream_in_status(session, date(2026, 9, 8), DreamStatus.RUNNING)
    service, _ = _service(session, [date(2026, 9, 8)])

    dream = await service.prepare_scheduled_run(date(2026, 9, 8))

    assert dream.status is DreamStatus.RUNNING


@pytest.mark.asyncio
async def test_preparing_a_fresh_day_creates_one_pending_dream(session) -> None:
    service, _ = _service(session, [date(2026, 9, 8)])

    first = await service.prepare_scheduled_run(date(2026, 9, 8))
    second = await service.prepare_scheduled_run(date(2026, 9, 8))

    assert first.status is DreamStatus.PENDING
    assert second.task_id == first.task_id


@pytest.mark.asyncio
async def test_the_manual_button_takes_the_newest_day_that_needs_one(
    session,
) -> None:
    days = [date(2026, 9, 8), date(2026, 9, 7)]
    await _dream_in_status(session, date(2026, 9, 8), DreamStatus.COMPLETED)
    service, _ = _service(session, days)

    dream = await service.prepare_manual_run()

    assert dream.target_date == date(2026, 9, 7)


@pytest.mark.asyncio
async def test_the_manual_button_re_runs_the_newest_day_when_none_are_due(
    session,
) -> None:
    """Pressed deliberately, so it should do something rather than nothing."""

    await _dream_in_status(session, date(2026, 9, 8), DreamStatus.COMPLETED)
    service, _ = _service(session, [date(2026, 9, 8)])

    dream = await service.prepare_manual_run()

    assert dream.target_date == date(2026, 9, 8)
    assert dream.status is DreamStatus.PENDING


@pytest.mark.parametrize(
    "value", ["16:00", "18:30", "23:59", "00:00", "00:30", "08:00"]
)
def test_times_outside_the_trading_session_are_accepted(value: str) -> None:
    from backend.business.settings import normalize_dream_schedule_time

    assert normalize_dream_schedule_time(value) == value


@pytest.mark.parametrize(
    "value", ["08:01", "09:30", "11:00", "13:00", "15:00", "15:59"]
)
def test_times_inside_the_trading_session_are_refused(value: str) -> None:
    """A dream then would mark a day done while it is still being traded."""

    from backend.business.settings import normalize_dream_schedule_time

    with pytest.raises(ValueError, match="16:00"):
        normalize_dream_schedule_time(value)


def test_a_stored_time_outside_the_window_still_loads() -> None:
    """The window narrowed after rows were written; loading must not break."""

    from backend.business.settings import DEFAULT_DREAM_SCHEDULE_TIME
    from backend.infra.repositories.settings_repo import _loadable_dream_time

    assert _loadable_dream_time("12:00") == DEFAULT_DREAM_SCHEDULE_TIME
    assert _loadable_dream_time("23:15") == "23:15"
