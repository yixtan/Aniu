"""Application service for the system-status panel."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, time, timedelta

from backend.business.memories.models import MemoryActivityOperation
from backend.business.shared.enums import RunStatus
from backend.business.system_status.dto import (
    DailyStatusDTO,
    DreamStatusDTO,
    SystemStatusDTO,
    TokenDayDTO,
)
from backend.business.system_status.models import (
    MARKET_TIMEZONE,
    MEMORY_WRITE_TOOL,
    STATUS_WINDOW_DAYS,
    TOKEN_WINDOW_DAYS,
    TRADE_TOOL,
    DataCallFact,
    DreamFact,
    MemoryActivityFact,
    RunFact,
    ToolCallFact,
    is_dream_task,
    market_day,
)
from backend.business.system_status.ports import SystemStatusRepositoryPort


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _start_of(day: date) -> datetime:
    """Midnight opening a market day, as the UTC moment stored rows compare to."""

    return datetime.combine(day, time.min, tzinfo=MARKET_TIMEZONE).astimezone(UTC)


def _by_day[T](
    facts: Iterable[T], moment: Callable[[T], datetime]
) -> defaultdict[date, list[T]]:
    grouped: defaultdict[date, list[T]] = defaultdict(list)
    for fact in facts:
        grouped[market_day(moment(fact))].append(fact)
    return grouped


def _fold_day(
    day: date,
    runs: list[RunFact],
    tool_calls: list[ToolCallFact],
    data_calls: list[DataCallFact],
    reads: list[MemoryActivityFact],
) -> DailyStatusDTO:
    completed = [run for run in runs if run.status == RunStatus.COMPLETED.value]
    trades = [call for call in tool_calls if call.tool_name == TRADE_TOOL]
    writes = [call for call in tool_calls if call.tool_name == MEMORY_WRITE_TOOL]
    return DailyStatusDTO(
        day=day,
        runs_completed=len(completed),
        runs_failed=sum(run.status == RunStatus.FAILED.value for run in runs),
        summaries_html=sum(run.summary_html for run in completed),
        tokens=sum(run.total_tokens for run in runs),
        trades_completed=sum(call.succeeded for call in trades),
        trades_failed=sum(not call.succeeded for call in trades),
        memory_writes=sum(call.succeeded for call in writes),
        memory_write_failures=sum(not call.succeeded for call in writes),
        memory_reads=len(reads),
        # The same words every quarter hour fetch the same memories; how many
        # different questions were asked says more than how many times.
        memory_distinct_queries=len({read.query.strip() for read in reads}),
        data_calls=len(data_calls),
        data_call_failures=sum(not call.succeeded for call in data_calls),
    )


class SystemStatusService:
    """Fold raw activity into one row per market day.

    Every window is a fixed run of calendar days ending today, zeros
    included: a trading day with nothing in it is exactly the kind of day the
    panel exists to show, and only a person can tell a holiday from a crash.
    """

    def __init__(
        self,
        repository: SystemStatusRepositoryPort,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or _utc_now

    async def overview(self) -> SystemStatusDTO:
        now = self._clock()
        today = market_day(now)
        status_days = [
            today - timedelta(days=offset) for offset in range(STATUS_WINDOW_DAYS)
        ]
        token_days = [
            today - timedelta(days=offset) for offset in range(TOKEN_WINDOW_DAYS)
        ]
        status_since = _start_of(status_days[-1])

        # Runs are read once for the longer window; the status rows take the
        # slice they need from the same list.
        runs = await self._repository.runs_since(_start_of(token_days[-1]))
        tool_calls = await self._repository.tool_calls_since(status_since)
        data_calls = await self._repository.data_calls_since(status_since)
        activities = await self._repository.memory_activities_since(status_since)
        dream = await self._repository.latest_dream()
        inventory = await self._repository.memory_inventory()

        runs_by_day = _by_day(runs, lambda fact: fact.started_at)
        tools_by_day = _by_day(tool_calls, lambda fact: fact.created_at)
        data_by_day = _by_day(data_calls, lambda fact: fact.created_at)
        # Writes are counted from tool invocations (which also record
        # failures); activities only add what the runs asked memory for.
        reads_by_day = _by_day(
            (
                activity
                for activity in activities
                if activity.operation == MemoryActivityOperation.READ.value
                and not is_dream_task(activity.task_id)
            ),
            lambda fact: fact.created_at,
        )

        days = [
            _fold_day(
                day,
                runs_by_day[day],
                tools_by_day[day],
                data_by_day[day],
                reads_by_day[day],
            )
            for day in status_days
        ]
        tokens = [
            TokenDayDTO(
                day=day,
                tokens=sum(run.total_tokens for run in runs_by_day[day]),
                runs=len(runs_by_day[day]),
            )
            for day in token_days
        ]
        return SystemStatusDTO(
            generated_at=now,
            days=days,
            tokens=tokens,
            latest_dream=None if dream is None else await self._describe(dream),
            memory_live=inventory.live,
            memory_with_lineage=inventory.with_lineage,
        )

    async def _describe(self, dream: DreamFact) -> DreamStatusDTO:
        activities = await self._repository.memory_activities_for_task(dream.task_id)
        counts = Counter(activity.operation for activity in activities)
        return DreamStatusDTO(
            target_date=dream.target_date,
            status=dream.status,
            completed_at=dream.completed_at,
            failure_reason=dream.failure_reason,
            created=counts[MemoryActivityOperation.CREATE.value],
            updated=counts[MemoryActivityOperation.UPDATE.value],
            deleted=counts[MemoryActivityOperation.DELETE.value],
        )


__all__ = ["SystemStatusService"]
