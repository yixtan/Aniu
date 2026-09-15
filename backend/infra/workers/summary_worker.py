"""The render lane: HTML summaries, rendered beside the run worker."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.runs.execution import RunReport
from backend.business.runs.executor import RunExecutor

logger = logging.getLogger(__name__)

SUMMARY_QUEUE_MAXSIZE = 64


class SummaryWorker:
    """Finish parked analyses without holding the trading account.

    The run worker executes one job at a time on purpose: two runs trading at
    once is the failure the whole lease design exists to prevent. But an
    analysis stops being a trading run the moment its Run stage ends — what
    is left is turning a report that is already written into HTML, through a
    stage that reaches no tool at all. Keeping that on the exclusive lane cost
    an order watch its slot every time a render overlapped one, which for a
    healthy 75-second render and a three-minute watch cadence was most days.

    The queue is in memory, and that is the design, not a shortcut: what it
    carries is the run report with its raw tool evidence, and only a slimmed
    projection of that reaches the database. A process that dies with work
    queued here leaves runs parked in Summary, and startup completes them with
    the Markdown that was saved before the account was ever released.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        executor_factory: Callable[[AsyncSession], RunExecutor],
    ) -> None:
        self._session_factory = session_factory
        self._executor_factory = executor_factory
        self._queue: asyncio.Queue[tuple[int, RunReport]] = asyncio.Queue(
            maxsize=SUMMARY_QUEUE_MAXSIZE
        )
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done() and not self._stopping

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run_loop(), name="aniu-summary-worker")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def submit(self, run_id: int, report: RunReport) -> None:
        """Queue a parked run for rendering.

        A full queue is logged and dropped rather than awaited: blocking here
        would block the run worker, which is the one thing this lane exists to
        avoid. The run is already complete in substance and startup recovery
        will close it out with its Markdown.
        """

        if self._stopping:
            raise RuntimeError("summary worker is stopping")
        if self._task is None or self._task.done():
            self.start()
        try:
            self._queue.put_nowait((run_id, report))
        except asyncio.QueueFull:
            logger.warning(
                "summary queue is full; run stays parked with its Markdown report",
                extra={"run_id": run_id},
            )

    async def _run_loop(self) -> None:
        while not self._stopping:
            run_id, report = await self._queue.get()
            try:
                await self._render(run_id, report)
            except Exception:
                logger.exception(
                    "summary rendering failed",
                    extra={"run_id": run_id},
                )
            finally:
                self._queue.task_done()

    async def _render(self, run_id: int, report: RunReport) -> None:
        async with self._session_factory() as session:
            # No execution fence here: the durable job that owned this run was
            # closed when the account was released, so there is no claim left
            # to check against. Nothing else writes this row — the run worker
            # has moved on and a new run gets a new id.
            executor = self._executor_factory(session)
            await executor.render_summary(run_id, report)


__all__ = ["SUMMARY_QUEUE_MAXSIZE", "SummaryWorker"]
