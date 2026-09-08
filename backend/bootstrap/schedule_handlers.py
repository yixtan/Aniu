"""Schedule/account handlers wired into the infrastructure JobRunner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.account.service import AccountAppService
from backend.business.dreams import DreamService, DreamStatus
from backend.business.runs.commands import StartRunCommand
from backend.business.runs.service import RunService
from backend.business.schedules import StrategySchedule
from backend.business.shared.enums import TriggerSource

LeaseCheck = Callable[[], Awaitable[None]]


def build_market_analysis_handler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    run_service_factory: Callable[[AsyncSession], RunService],
    enqueue_run: Callable[[int], Awaitable[None]] | None = None,
) -> Callable[[StrategySchedule, LeaseCheck], Awaitable[None]]:
    """Submit a durable run job; execution is done by the lease worker."""

    async def handler(schedule: StrategySchedule, lease_check: LeaseCheck) -> None:
        async with session_factory() as session:
            service = run_service_factory(session)
            created = await service.create_run(
                StartRunCommand(
                    trigger_source=TriggerSource.SCHEDULED,
                    schedule_id=schedule.schedule_id,
                ),
                execution_guard=lease_check,
            )
        if enqueue_run is not None:
            await lease_check()
            await enqueue_run(created.run_id)

    return handler


def build_memory_dream_handler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    dream_service_factory: Callable[[AsyncSession], DreamService],
    enqueue_dream: Callable[[int], Awaitable[None]] | None = None,
) -> Callable[[LeaseCheck], Awaitable[None]]:
    """Queue a dream for every recent run day that still needs one.

    Usually that is a single day and this behaves as it always did. It is more
    only after the trigger was missed — the machine asleep, the process down —
    and those days would otherwise never be reflected on, because the day a
    dream covers used to be read off the clock rather than off the runs.

    The worker consumes its queue one task at a time, so queueing several here
    does not put several agents on the model at once.
    """

    async def handler(lease_check: LeaseCheck) -> None:
        async with session_factory() as session:
            service = dream_service_factory(session)
            targets = await service.pending_target_dates()

        for target_date in targets:
            await lease_check()
            async with session_factory() as session:
                dream = await dream_service_factory(session).prepare_scheduled_run(
                    target_date, execution_guard=lease_check
                )
            # A dream already running is left alone: it is being worked on.
            if dream.status is DreamStatus.PENDING and enqueue_dream is not None:
                await lease_check()
                await enqueue_dream(dream.task_id)

    return handler


def build_account_refresh_handler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    account_service_factory: Callable[[AsyncSession], Awaitable[AccountAppService]],
) -> Callable[..., Awaitable[None]]:
    async def handler(lease_check: LeaseCheck) -> None:
        async with session_factory() as session:
            service = await account_service_factory(session)
            await service.refresh_account_cache(execution_guard=lease_check)

    return handler
