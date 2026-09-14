"""Tests for extracting daily Run reports for the Dream Agent."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from backend.business.shared.enums import RunStatus
from backend.infra.db.models import StrategyRunModel
from backend.infra.repositories.run_repo import RunRepository


@pytest.mark.asyncio
async def test_completed_report_query_uses_shanghai_completion_date(session) -> None:
    session.add(
        StrategyRunModel(
            id=20260818101,
            trigger_source="manual",
            status=RunStatus.COMPLETED.value,
            current_state="Completed",
            snapshot_json={},
            trace_json={
                "stages": [
                    {
                        "stage_id": "Run",
                        "key": "run",
                        "steps": [
                            {
                                "type": "result",
                                "content": "## 当日运行报告",
                            }
                        ],
                    }
                ]
            },
            started_at="2026-08-18T14:50:00+00:00",
            completed_at="2026-08-18T15:10:00+00:00",
        )
    )
    await session.commit()

    reports = await RunRepository(session).list_completed_reports(
        datetime(2026, 8, 18, tzinfo=UTC).date()
    )

    assert len(reports) == 1
    assert reports[0].run_id == 20260818101
    assert reports[0].content == "## 当日运行报告"


def _clock(day: str, minute: int, second: int) -> str:
    return f"{day}T{minute // 60:02d}:{minute % 60:02d}:{second:02d}+00:00"


def _analysis(run_id: int, *, minute: int, day: str = "2026-09-14") -> StrategyRunModel:
    return StrategyRunModel(
        id=run_id,
        trigger_source="scheduled",
        status=RunStatus.COMPLETED.value,
        current_state="Completed",
        snapshot_json={},
        trace_json={
            "stages": [
                {
                    "stage_id": "Run",
                    "key": "run",
                    "steps": [{"type": "result", "content": f"报告 {run_id}"}],
                }
            ]
        },
        started_at=_clock(day, minute, 0),
        completed_at=_clock(day, minute, 30),
    )


def _watch(run_id: int, *, minute: int, day: str = "2026-09-14") -> StrategyRunModel:
    return StrategyRunModel(
        id=run_id,
        trigger_source="scheduled",
        status=RunStatus.COMPLETED.value,
        current_state="Completed",
        snapshot_json={},
        trace_json={
            "stages": [
                {
                    "stage_id": "Watch",
                    "key": "watch",
                    "steps": [{"type": "result", "content": f"盯盘记录 {run_id}"}],
                }
            ]
        },
        started_at=_clock(day, minute, 10),
        completed_at=_clock(day, minute, 20),
    )


@pytest.mark.asyncio
async def test_a_page_of_reports_is_full_even_on_a_day_full_of_watches(
    session,
) -> None:
    """The bug this exists for, and it was silent.

    LIMIT applies to rows, so a page of ten rows that were mostly watches came
    back as two reports — and `has_more`, which the tool computes by comparing
    what came back against what was asked for, then told the dream there was
    nothing left with nine reports still unread. It would have reflected on a
    fifth of the day and said nothing was wrong.
    """

    for index in range(12):
        session.add(_analysis(20260914101 + index, minute=90 + index * 20))
    for index in range(60):
        session.add(_watch(20260914301 + index, minute=93 + index * 3))
    await session.commit()

    repo = RunRepository(session)
    first = await repo.list_completed_reports(date(2026, 9, 14), limit=10, offset=0)
    second = await repo.list_completed_reports(date(2026, 9, 14), limit=10, offset=10)

    assert len(first) == 10
    assert len(second) == 2
    # What the tool reports as `has_more`, spelled out at the boundary it broke.
    assert (len(first) == 10) is True
    assert (len(second) == 10) is False


@pytest.mark.asyncio
async def test_a_watch_record_is_not_a_report_to_reflect_on(session) -> None:
    """It logs which resting orders were left alone, not what the day meant."""

    session.add(_analysis(20260914101, minute=90))
    session.add(_watch(20260914301, minute=93))
    await session.commit()

    reports = await RunRepository(session).list_completed_reports(date(2026, 9, 14))

    assert [report.run_id for report in reports] == [20260914101]


@pytest.mark.asyncio
async def test_a_day_of_watches_alone_is_not_a_day_to_reflect_on(session) -> None:
    """Picking a date and reading that date's reports have to agree on which
    runs count, or the dream spends a whole turn finding nothing to read."""

    session.add(_watch(20260914301, minute=93))
    session.add(_analysis(20260911101, minute=90, day="2026-09-11"))
    await session.commit()

    days = await RunRepository(session).recent_days_with_runs(limit=3)

    assert date(2026, 9, 14) not in days
