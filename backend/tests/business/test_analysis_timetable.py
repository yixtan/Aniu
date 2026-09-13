"""The analysis schedule is one fixed timetable per interval."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.api.schemas.schedule import CreateScheduleRequest
from backend.business.schedules import (
    ANALYSIS_INTERVAL_CHOICES,
    ANALYSIS_TIMETABLE,
    MARKET_ANALYSIS_TASK_TYPE,
    ORDER_WATCH_TASK_TYPE,
    derive_intraday_schedule_times,
)

OPEN = ("09:30", "13:00")
LATEST = ("11:20", "14:50")
TAIL_GAP = 15


def _minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _rule(interval: int) -> tuple[str, ...]:
    """The rule the hand-written rows are meant to obey, derived on its own."""

    times: list[str] = []
    for open_at, latest in zip(OPEN, LATEST, strict=True):
        current = _minutes(open_at)
        session: list[int] = []
        while current <= _minutes(latest):
            session.append(current)
            current += interval
        if _minutes(latest) - session[-1] >= TAIL_GAP:
            session.append(_minutes(latest))
        times.extend(f"{m // 60:02d}:{m % 60:02d}" for m in session)
    return tuple(times)


def test_the_choices_run_from_fifteen_to_sixty_in_fives() -> None:
    assert ANALYSIS_INTERVAL_CHOICES == (15, 20, 25, 30, 35, 40, 45, 50, 55, 60)
    assert set(ANALYSIS_TIMETABLE) == set(ANALYSIS_INTERVAL_CHOICES)


@pytest.mark.parametrize("interval", ANALYSIS_INTERVAL_CHOICES)
def test_every_row_obeys_the_rule_it_was_written_from(interval: int) -> None:
    """Rows are literal so they can be read one by one; this keeps them honest."""

    assert ANALYSIS_TIMETABLE[interval] == _rule(interval)


@pytest.mark.parametrize("interval", ANALYSIS_INTERVAL_CHOICES)
def test_every_row_opens_each_session_and_never_starts_too_late(interval: int) -> None:
    row = ANALYSIS_TIMETABLE[interval]
    morning = [t for t in row if t < "12:00"]
    afternoon = [t for t in row if t >= "12:00"]
    assert (morning[0], afternoon[0]) == OPEN
    # Ten minutes before the bell: a run normally thinks for two to three.
    assert morning[-1] <= "11:20" and afternoon[-1] <= "14:50"
    assert row == tuple(sorted(row))


def test_a_longer_interval_never_means_more_runs() -> None:
    counts = [len(ANALYSIS_TIMETABLE[i]) for i in ANALYSIS_INTERVAL_CHOICES]
    assert counts == sorted(counts, reverse=True)
    # Twenty minutes used to lose a run each session; it no longer does.
    assert len(ANALYSIS_TIMETABLE[20]) == 12


def test_the_timetable_is_what_an_analysis_schedule_derives() -> None:
    assert derive_intraday_schedule_times(20, MARKET_ANALYSIS_TASK_TYPE) == (
        ANALYSIS_TIMETABLE[20]
    )


def test_a_legacy_interval_still_derives_rather_than_failing_to_load() -> None:
    """A stored row must never break over a value the API no longer accepts."""

    times = derive_intraday_schedule_times(17, MARKET_ANALYSIS_TASK_TYPE)

    assert times[0] == "09:30"
    assert all(t <= "14:50" for t in times)


def test_the_api_accepts_only_the_fixed_set_for_an_analysis() -> None:
    CreateScheduleRequest(task_type=MARKET_ANALYSIS_TASK_TYPE, interval_minutes=45)
    with pytest.raises(ValidationError, match="one of"):
        CreateScheduleRequest(task_type=MARKET_ANALYSIS_TASK_TYPE, interval_minutes=17)
    # The watch keeps a free interval above its floor.
    CreateScheduleRequest(task_type=ORDER_WATCH_TASK_TYPE, interval_minutes=17)


def test_the_watch_is_untouched_by_the_analysis_timetable() -> None:
    times = derive_intraday_schedule_times(3, ORDER_WATCH_TASK_TYPE)
    assert len(times) == 80
    assert (times[0], times[-1]) == ("09:33", "15:00")
