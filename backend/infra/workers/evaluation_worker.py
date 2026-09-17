"""The evaluation lane: independent reviews, beside the run worker."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.evaluations.service import EvaluationService

logger = logging.getLogger(__name__)

EVALUATION_QUEUE_MAXSIZE = 32


class EvaluationWorker:
    """Run reviews without ever queueing a run job.

    Deliberately not `run_jobs`. That table takes `active_guard` when a row is
    *created*, not when it is claimed, and the guard is unique — so an
    evaluation submitted while an analysis was running would not wait its
    turn, it would be refused. The same reasoning put the render lane here.

    Safe to run beside an order watch because an evaluation reaches no tool
    that can place or cancel an order: it reads a finished run's own report
    and the account's order record, and writes only its own row.

    The queue is in memory. A process that dies with work queued here loses
    the request, which costs a button press — there is no trade half-done and
    nothing to recover, which is why this needs no startup sweep.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        service_factory: Callable[[AsyncSession], EvaluationService],
    ) -> None:
        self._session_factory = session_factory
        self._service_factory = service_factory
        self._queue: asyncio.Queue[int] = asyncio.Queue(
            maxsize=EVALUATION_QUEUE_MAXSIZE
        )
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="aniu-evaluation-worker")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def submit(self, evaluation_id: int) -> None:
        await self._queue.put(evaluation_id)

    async def _loop(self) -> None:
        while True:
            evaluation_id = await self._queue.get()
            try:
                await self._execute(evaluation_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "evaluation worker execution failed",
                    extra={"evaluation_id": evaluation_id},
                )
            finally:
                self._queue.task_done()

    async def _execute(self, evaluation_id: int) -> None:
        async with self._session_factory() as session:
            await self._service_factory(session).execute(evaluation_id)


__all__ = ["EVALUATION_QUEUE_MAXSIZE", "EvaluationWorker"]
