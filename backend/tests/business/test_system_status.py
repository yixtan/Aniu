"""System status: what a day is, and what counts in it."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.business.system_status import (
    STATUS_WINDOW_DAYS,
    TOKEN_WINDOW_DAYS,
    DataCallFact,
    DreamFact,
    MemoryActivityFact,
    MemoryInventory,
    RunFact,
    SystemStatusService,
    ToolCallFact,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
# 20:00 in Shanghai on 09-09, after the last run of the day.
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
TODAY = date(2026, 9, 9)

RUN_TASK = 20260909101
DREAM_TASK = 20260908401


def at(day: date, hour: int, minute: int = 0) -> datetime:
    """A Shanghai wall-clock moment, stored the way rows are — as UTC."""

    return datetime.combine(day, time(hour, minute), tzinfo=SHANGHAI).astimezone(UTC)


def run(
    moment: datetime, *, status: str = "COMPLETED", html: bool = True, tokens: int = 0
) -> RunFact:
    return RunFact(
        started_at=moment, status=status, summary_html=html, total_tokens=tokens
    )


def read(
    moment: datetime, query: str, *, task_id: int = RUN_TASK
) -> MemoryActivityFact:
    return MemoryActivityFact(
        created_at=moment, operation="read", task_id=task_id, query=query
    )


class FakeRepository:
    """Answers with whatever facts the test hands it, filtered by `since` the
    way a store would, so the window arithmetic is exercised too."""

    def __init__(
        self,
        *,
        runs: list[RunFact] | None = None,
        tool_calls: list[ToolCallFact] | None = None,
        data_calls: list[DataCallFact] | None = None,
        activities: list[MemoryActivityFact] | None = None,
        dream: DreamFact | None = None,
        inventory: MemoryInventory = MemoryInventory(live=0, with_lineage=0),
    ) -> None:
        self.runs = runs or []
        self.tool_calls = tool_calls or []
        self.data_calls = data_calls or []
        self.activities = activities or []
        self.dream = dream
        self.inventory = inventory

    async def runs_since(self, since: datetime) -> list[RunFact]:
        return [fact for fact in self.runs if fact.started_at >= since]

    async def tool_calls_since(self, since: datetime) -> list[ToolCallFact]:
        return [fact for fact in self.tool_calls if fact.created_at >= since]

    async def data_calls_since(self, since: datetime) -> list[DataCallFact]:
        return [fact for fact in self.data_calls if fact.created_at >= since]

    async def memory_activities_since(
        self, since: datetime
    ) -> list[MemoryActivityFact]:
        return [fact for fact in self.activities if fact.created_at >= since]

    async def memory_activities_for_task(
        self, task_id: int
    ) -> list[MemoryActivityFact]:
        return [fact for fact in self.activities if fact.task_id == task_id]

    async def latest_dream(self) -> DreamFact | None:
        return self.dream

    async def memory_inventory(self) -> MemoryInventory:
        return self.inventory


def _service(repository: FakeRepository) -> SystemStatusService:
    return SystemStatusService(repository, clock=lambda: NOW)


@pytest.mark.asyncio
async def test_the_window_is_a_fixed_run_of_days_ending_today_zeros_included() -> None:
    """A trading day with nothing in it is the day the panel exists to show."""

    status = await _service(FakeRepository()).overview()

    assert [row.day for row in status.days] == [
        TODAY - timedelta(days=offset) for offset in range(STATUS_WINDOW_DAYS)
    ]
    assert all(row.runs_completed == 0 and row.data_calls == 0 for row in status.days)
    assert [row.day for row in status.tokens][:2] == [TODAY, TODAY - timedelta(days=1)]
    assert len(status.tokens) == TOKEN_WINDOW_DAYS
    assert status.latest_dream is None
    assert status.generated_at == NOW


@pytest.mark.asyncio
async def test_a_moment_after_midnight_utc_belongs_to_the_shanghai_day() -> None:
    """01:00 Shanghai on the 9th is still the 8th in UTC; the panel must not
    split one morning's runs across two rows."""

    early = datetime(2026, 9, 8, 17, 0, tzinfo=UTC)
    assert early.astimezone(SHANGHAI).date() == TODAY

    repository = FakeRepository(runs=[run(early, tokens=7)])
    status = await _service(repository).overview()

    today, yesterday = status.days[0], status.days[1]
    assert (today.runs_completed, today.tokens) == (1, 7)
    assert (yesterday.runs_completed, yesterday.tokens) == (0, 0)


@pytest.mark.asyncio
async def test_a_day_folds_runs_tools_data_and_memory_reads() -> None:
    morning = at(TODAY, 9, 30)
    repository = FakeRepository(
        runs=[
            run(morning, tokens=100),
            run(at(TODAY, 10, 0), html=False, tokens=50),
            run(at(TODAY, 10, 30), status="FAILED", html=False, tokens=20),
            # Aborted by hand: neither a success nor a failure of the system.
            run(at(TODAY, 11, 0), status="ABORTED", html=False, tokens=5),
        ],
        tool_calls=[
            ToolCallFact(morning, "trade", True),
            ToolCallFact(morning, "trade", False),
            ToolCallFact(morning, "memory_write", True),
            ToolCallFact(morning, "memory_write", True),
            ToolCallFact(morning, "memory_write", False),
            ToolCallFact(morning, "memory_write", False),
            ToolCallFact(morning, "memory_write", False),
        ],
        data_calls=[DataCallFact(morning, True)] * 3
        + [DataCallFact(morning, False)] * 2,
        activities=[
            read(morning, "市场环境 仓位"),
            read(morning, " 市场环境 仓位 "),
            read(morning, "紫金矿业 减半"),
        ],
    )

    today = (await _service(repository).overview()).days[0]

    assert today.runs_completed == 2
    assert today.runs_failed == 1
    assert today.summaries_html == 1
    # Tokens were spent whatever became of the run.
    assert today.tokens == 175
    assert (today.trades_completed, today.trades_failed) == (1, 1)
    assert (today.memory_writes, today.memory_write_failures) == (2, 3)
    assert today.memory_reads == 3
    # Whitespace does not make a different question.
    assert today.memory_distinct_queries == 2
    assert (today.data_calls, today.data_call_failures) == (5, 2)


@pytest.mark.asyncio
async def test_the_curators_reads_are_not_the_runs() -> None:
    """The nightly dream reads memory too; counting it would hide a day on
    which no run asked memory anything."""

    repository = FakeRepository(
        activities=[
            read(at(TODAY, 9, 30), "仓位", task_id=RUN_TASK),
            read(at(TODAY, 23, 35), "仓位", task_id=DREAM_TASK),
            # Before task numbering existed, activities carried 0.
            read(at(TODAY, 9, 45), "止损", task_id=0),
        ]
    )

    today = (await _service(repository).overview()).days[0]

    assert today.memory_reads == 2


@pytest.mark.asyncio
async def test_the_latest_dream_reports_what_it_did_to_memory() -> None:
    dream = DreamFact(
        task_id=DREAM_TASK,
        target_date=date(2026, 9, 8),
        status="completed",
        completed_at=at(date(2026, 9, 8), 23, 32),
        failure_reason=None,
    )
    when = dream.completed_at
    assert when is not None
    ops = ["read", "create", "create", "update", "delete", "delete", "delete"]
    repository = FakeRepository(
        dream=dream,
        activities=[MemoryActivityFact(when, op, DREAM_TASK, "") for op in ops]
        + [MemoryActivityFact(when, "create", RUN_TASK, "")],
        inventory=MemoryInventory(live=53, with_lineage=2),
    )

    status = await _service(repository).overview()

    assert status.latest_dream is not None
    assert status.latest_dream.target_date == date(2026, 9, 8)
    assert status.latest_dream.status == "completed"
    assert (
        status.latest_dream.created,
        status.latest_dream.updated,
        status.latest_dream.deleted,
    ) == (2, 1, 3)
    assert (status.memory_live, status.memory_with_lineage) == (53, 2)


@pytest.mark.asyncio
async def test_tokens_span_a_month_while_status_spans_a_week() -> None:
    old_day = TODAY - timedelta(days=20)
    repository = FakeRepository(runs=[run(at(old_day, 10, 0), tokens=999)])

    status = await _service(repository).overview()

    assert old_day not in {row.day for row in status.days}
    by_day = {row.day: row for row in status.tokens}
    assert (by_day[old_day].tokens, by_day[old_day].runs) == (999, 1)
    assert by_day[TODAY].tokens == 0
