"""Application service for idempotent nightly memory dreams."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from backend.business.dreams.models import DreamStatus, MemoryDream
from backend.business.dreams.ports import (
    DreamAgentPort,
    DreamRepositoryPort,
    RunDayQueryPort,
)
from backend.business.shared.ports import CommitterPort

logger = logging.getLogger(__name__)
ExecutionGuard = Callable[[], Awaitable[None]]
ExecutionFence = Callable[[MemoryDream, DreamStatus], Awaitable[bool]]


class ExecutionFencedError(RuntimeError):
    """Raised when a leased dream can no longer persist its state."""


_MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
DREAM_BACKFILL_DAYS = 3
"""How many recent run days one trigger considers.

The lookback window and the per-trigger cap are deliberately the same
number. A window wider than the cap promises a backfill the trigger cannot
reach: the days beyond it slide out of range before anything gets to them,
and nothing says so.
"""


class DreamService:
    def __init__(
        self,
        repository: DreamRepositoryPort,
        agent: DreamAgentPort | None = None,
        *,
        committer: CommitterPort | None = None,
        run_days: RunDayQueryPort | None = None,
    ) -> None:
        self._repository = repository
        self._agent = agent
        self._run_days = run_days
        self._committer = committer

    async def create_or_get(
        self,
        target_date: date,
        *,
        execution_guard: ExecutionGuard | None = None,
    ) -> MemoryDream:
        existing = await self._repository.get_by_date(target_date)
        if existing is not None:
            return existing
        dream = MemoryDream(
            task_id=await self._repository.next_task_id(target_date),
            target_date=target_date,
        )
        try:
            stored = await self._repository.add(dream)
            await self._commit(execution_guard=execution_guard)
            return stored
        except Exception:
            await self._rollback()
            existing = await self._repository.get_by_date(target_date)
            if existing is not None:
                return existing
            raise

    async def pending_target_dates(
        self, *, limit: int = DREAM_BACKFILL_DAYS
    ) -> list[date]:
        """Recent run days that no completed dream covers, newest first.

        Days without runs are skipped. There is no report to reflect on, and a
        dream over an empty day still spends a full agent turn to conclude it
        had nothing to do.

        A failed or still-running dream leaves its day pending: the question is
        whether the day has been reflected on, not whether a row exists for it.
        """

        if self._run_days is None:
            return []
        days = await self._run_days.recent_days_with_runs(limit=limit)
        pending: list[date] = []
        for day in days:
            existing = await self._repository.get_by_date(day)
            if existing is None or existing.status is not DreamStatus.COMPLETED:
                pending.append(day)
        return pending

    async def prepare_scheduled_run(
        self,
        target_date: date,
        *,
        execution_guard: ExecutionGuard | None = None,
    ) -> MemoryDream:
        """Bring this date's dream to a state the worker can pick up.

        A failed dream is put back to pending. Eligibility asks whether a day
        has a completed dream, so a failure left alone would be selected every
        night and enqueued none of them — the day would be picked forever and
        never run.

        A running dream is returned untouched, so a trigger firing while one is
        in flight cannot start it twice.
        """

        dream = await self.create_or_get(target_date, execution_guard=execution_guard)
        if dream.status is DreamStatus.FAILED:
            dream.retry()
            saved = await self._repository.save(dream)
            await self._commit(execution_guard=execution_guard)
            return saved
        return dream

    async def prepare_manual_run(self, *, now: datetime | None = None) -> MemoryDream:
        """Dream the most recent day that still needs one.

        When every recent day is covered the newest is re-run instead: the
        button was pressed deliberately, so it should do something.
        """

        target_date = await self._manual_target_date(now=now)
        dream = await self.create_or_get(target_date)
        if dream.status in {DreamStatus.COMPLETED, DreamStatus.FAILED}:
            dream.retry()
            return await self._save_and_commit(dream)
        return dream

    async def _manual_target_date(self, *, now: datetime | None) -> date:
        pending = await self.pending_target_dates()
        if pending:
            return pending[0]
        if self._run_days is not None:
            recent = await self._run_days.recent_days_with_runs(limit=1)
            if recent:
                return recent[0]
        # No run history to go on, so keep the original meaning of the button.
        current = (now or datetime.now(tz=UTC)).astimezone(_MARKET_TIMEZONE)
        return current.date() - timedelta(days=1)

    async def list_recent(
        self, *, limit: int, offset: int
    ) -> tuple[list[MemoryDream], int]:
        total = await self._repository.count()
        items = await self._repository.list_recent(limit=limit, offset=offset)
        return items, total

    async def get_by_id(self, task_id: int) -> MemoryDream | None:
        return await self._repository.get_by_id(task_id)

    async def delete(self, task_id: int) -> bool:
        deleted = await self._repository.delete(task_id)
        if deleted:
            await self._commit()
        return deleted

    async def execute(
        self,
        task_id: int,
        *,
        execution_fence: ExecutionFence | None = None,
    ) -> MemoryDream | None:
        if self._agent is None:
            raise RuntimeError("dream agent is not configured")
        dream = await self._repository.get_by_id(task_id)
        if dream is None or dream.status is not DreamStatus.PENDING:
            return dream
        dream.start()
        await self._save_and_commit(
            dream,
            execution_fence=execution_fence,
            expected_status=DreamStatus.PENDING,
        )
        try:
            result = await self._agent.run(dream)
        except asyncio.CancelledError:
            try:
                interrupted = await self._repository.get_by_id(task_id)
                if (
                    interrupted is not None
                    and interrupted.status is DreamStatus.RUNNING
                ):
                    interrupted.fail("梦境任务被进程取消，未自动重试，请确认后手动重试")
                    await self._save_and_commit(
                        interrupted,
                        execution_fence=execution_fence,
                        expected_status=DreamStatus.RUNNING,
                    )
            except ExecutionFencedError:
                logger.info(
                    "memory dream lease fence rejected cancellation state",
                    extra={"task_id": task_id},
                )
            except Exception:  # noqa: BLE001 - preserve cancellation semantics
                logger.exception(
                    "failed to persist cancelled memory dream",
                    extra={"task_id": task_id},
                )
            raise
        except ExecutionFencedError:
            raise
        except Exception as exc:  # noqa: BLE001 - persist task failure state
            logger.exception(
                "memory dream execution failed",
                extra={"task_id": task_id},
            )
            failed = await self._repository.get_by_id(task_id)
            if failed is not None and failed.status is DreamStatus.RUNNING:
                failed.fail(str(exc))
                await self._save_and_commit(
                    failed,
                    execution_fence=execution_fence,
                    expected_status=DreamStatus.RUNNING,
                )
                return failed
            raise
        completed = await self._repository.get_by_id(task_id)
        if completed is None:
            return None
        if completed.status is DreamStatus.RUNNING:
            completed.complete(result.content, result.total_tokens)
            await self._save_and_commit(
                completed,
                execution_fence=execution_fence,
                expected_status=DreamStatus.RUNNING,
            )
        return completed

    async def _save_and_commit(
        self,
        dream: MemoryDream,
        *,
        execution_fence: ExecutionFence | None = None,
        expected_status: DreamStatus | None = None,
    ) -> MemoryDream:
        if execution_fence is None:
            saved = await self._repository.save(dream)
        else:
            if expected_status is None:
                raise ValueError("fenced dream saves require an expected status")
            if not await execution_fence(dream, expected_status):
                raise ExecutionFencedError(
                    f"dream execution fence rejected task_id={dream.task_id}"
                )
            saved = dream
        await self._commit()
        return saved

    async def _commit(self, *, execution_guard: ExecutionGuard | None = None) -> None:
        if execution_guard is not None:
            await execution_guard()
        if self._committer is not None:
            await self._committer.commit()

    async def _rollback(self) -> None:
        rollback = getattr(self._committer, "rollback", None)
        if callable(rollback):
            await rollback()


__all__ = ["DreamService", "ExecutionFencedError"]
