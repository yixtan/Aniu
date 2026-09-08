"""What the nightly trigger queues, and in what order."""

from __future__ import annotations

from datetime import date

import pytest

from backend.bootstrap.schedule_handlers import build_memory_dream_handler
from backend.business.dreams import DreamStatus
from backend.business.dreams.service import DreamService
from backend.infra.repositories.memory_dream_repo import MemoryDreamRepository


class FakeRunDays:
    def __init__(self, days: list[date]) -> None:
        self._days = days

    async def recent_days_with_runs(self, *, limit: int) -> list[date]:
        return self._days[:limit]


def _handler(session_factory, days: list[date], queued: list[int]):
    def service_factory(session) -> DreamService:
        return DreamService(
            MemoryDreamRepository(session),
            committer=session,
            run_days=FakeRunDays(days),
        )

    async def enqueue(task_id: int) -> None:
        queued.append(task_id)

    return build_memory_dream_handler(
        session_factory=session_factory,
        dream_service_factory=service_factory,
        enqueue_dream=enqueue,
    )


async def _noop_lease() -> None:
    return None


@pytest.mark.asyncio
async def test_one_uncovered_day_queues_one_dream(session_factory) -> None:
    queued: list[int] = []
    handler = _handler(session_factory, [date(2026, 9, 8)], queued)

    await handler(_noop_lease)

    assert len(queued) == 1


@pytest.mark.asyncio
async def test_a_backlog_is_queued_newest_first(session_factory) -> None:
    """The most recent day carries the freshest lessons, so it goes first."""

    queued: list[int] = []
    days = [date(2026, 9, 8), date(2026, 9, 7), date(2026, 9, 4)]
    handler = _handler(session_factory, days, queued)

    await handler(_noop_lease)

    assert len(queued) == 3
    async with session_factory() as session:
        repository = MemoryDreamRepository(session)
        targets = []
        for task_id in queued:
            dream = await repository.get_by_id(task_id)
            assert dream is not None
            targets.append(dream.target_date)
    assert targets == days


@pytest.mark.asyncio
async def test_a_second_trigger_queues_nothing_new(session_factory) -> None:
    """Two triggers in one night must not run the same day twice."""

    queued: list[int] = []
    handler = _handler(session_factory, [date(2026, 9, 8)], queued)

    await handler(_noop_lease)
    first_round = list(queued)
    await handler(_noop_lease)

    # Still pending, so it is queued again for the worker to pick up — but it
    # is the same task, and the worker drops a duplicate id.
    assert set(queued) == set(first_round)


@pytest.mark.asyncio
async def test_a_running_dream_is_not_queued_again(session_factory) -> None:
    queued: list[int] = []
    target = date(2026, 9, 8)
    async with session_factory() as session:
        service = DreamService(
            MemoryDreamRepository(session),
            committer=session,
            run_days=FakeRunDays([target]),
        )
        dream = await service.create_or_get(target)
        dream.start()
        await MemoryDreamRepository(session).save(dream)
        await session.commit()

    handler = _handler(session_factory, [target], queued)
    await handler(_noop_lease)

    assert queued == []


@pytest.mark.asyncio
async def test_a_failed_dream_is_retried_by_the_next_trigger(session_factory) -> None:
    queued: list[int] = []
    target = date(2026, 9, 8)
    async with session_factory() as session:
        service = DreamService(
            MemoryDreamRepository(session),
            committer=session,
            run_days=FakeRunDays([target]),
        )
        dream = await service.create_or_get(target)
        dream.start()
        dream.fail("模型超时")
        await MemoryDreamRepository(session).save(dream)
        await session.commit()

    handler = _handler(session_factory, [target], queued)
    await handler(_noop_lease)

    assert len(queued) == 1
    async with session_factory() as session:
        stored = await MemoryDreamRepository(session).get_by_date(target)
    assert stored is not None
    assert stored.status is DreamStatus.PENDING


@pytest.mark.asyncio
async def test_no_run_days_queues_nothing(session_factory) -> None:
    queued: list[int] = []
    handler = _handler(session_factory, [], queued)

    await handler(_noop_lease)

    assert queued == []
