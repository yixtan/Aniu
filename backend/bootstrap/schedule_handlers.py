"""Schedule/account handlers wired into the infrastructure JobRunner."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.account.service import AccountAppService
from backend.business.dreams import DreamService, DreamStatus
from backend.business.runs.commands import StartRunCommand, StartWatchCommand
from backend.business.runs.numbering import is_order_watch_task
from backend.business.runs.service import RunService
from backend.business.schedules import StrategySchedule
from backend.business.shared import ConcurrentRunError
from backend.business.shared.enums import TriggerSource

logger = logging.getLogger(__name__)
LeaseCheck = Callable[[], Awaitable[None]]

# How long a scheduled analysis will wait for an order watch to finish.
# A watch is a couple of tool calls and is usually over in seconds; an
# analysis fires every twenty minutes at a fixed time and is not retried.
# Without this, a watch mid-flight at the wrong second would cost the whole
# analysis slot, silently, about once every eighteen trading days.
ANALYSIS_WAITS_FOR_WATCH_SECONDS = 30.0
_WAIT_POLL_SECONDS = 2.0


def build_market_analysis_handler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    run_service_factory: Callable[[AsyncSession], RunService],
    enqueue_run: Callable[[int], Awaitable[None]] | None = None,
) -> Callable[[StrategySchedule, LeaseCheck], Awaitable[None]]:
    """Submit a durable run job; execution is done by the lease worker."""

    async def handler(schedule: StrategySchedule, lease_check: LeaseCheck) -> None:
        command = StartRunCommand(
            trigger_source=TriggerSource.SCHEDULED,
            schedule_id=schedule.schedule_id,
        )
        deadline = asyncio.get_running_loop().time() + ANALYSIS_WAITS_FOR_WATCH_SECONDS
        while True:
            try:
                async with session_factory() as session:
                    service = run_service_factory(session)
                    created = await service.create_run(
                        command, execution_guard=lease_check
                    )
                break
            except ConcurrentRunError as exc:
                # Only a watch is worth waiting out. Another analysis in flight
                # means this slot is genuinely taken, and waiting would only
                # stack a second one behind it.
                if not is_order_watch_task(exc.running_run_id):
                    raise
                if asyncio.get_running_loop().time() >= deadline:
                    logger.warning(
                        "scheduled analysis gave up waiting for order watch %s",
                        exc.running_run_id,
                    )
                    raise
                await lease_check()
                await asyncio.sleep(_WAIT_POLL_SECONDS)
        if enqueue_run is not None:
            await lease_check()
            await enqueue_run(created.run_id)

    return handler


def build_order_watch_handler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    run_service_factory: Callable[[AsyncSession], RunService],
    enqueue_run: Callable[[int], Awaitable[None]] | None = None,
) -> Callable[[StrategySchedule, LeaseCheck], Awaitable[None]]:
    """Submit an order-watch run, or nothing if a run is already active.

    A watch that finds a run in flight simply does not happen: the next pass
    is three minutes away and will read the same plan. Queueing it would only
    line up several passes to ask the same question of the same orders.
    """

    async def handler(schedule: StrategySchedule, lease_check: LeaseCheck) -> None:
        try:
            async with session_factory() as session:
                service = run_service_factory(session)
                created = await service.create_watch_run(
                    StartWatchCommand(schedule_id=schedule.schedule_id),
                    execution_guard=lease_check,
                )
        except ConcurrentRunError as exc:
            logger.info(
                "order watch skipped: run %s is active", exc.running_run_id
            )
            return
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
