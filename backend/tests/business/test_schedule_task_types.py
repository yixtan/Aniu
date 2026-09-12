"""Two kinds of schedule, each with its own cadence floor and session window."""

from __future__ import annotations

import pytest

from backend.business.schedules import (
    MARKET_ANALYSIS_TASK_TYPE,
    ORDER_WATCH_TASK_TYPE,
    StrategySchedule,
    cadence_for,
    derive_intraday_schedule_times,
)


def test_a_schedule_with_no_stated_kind_is_the_kind_that_always_existed() -> None:
    """Rows that predate the column must read back as themselves."""

    assert StrategySchedule(interval_minutes=20).task_type == MARKET_ANALYSIS_TASK_TYPE


def test_the_analysis_floor_still_holds_at_fifteen_minutes() -> None:
    with pytest.raises(ValueError, match="interval_minutes must be >= 15"):
        StrategySchedule(interval_minutes=3)


def test_an_order_watch_may_run_every_three_minutes() -> None:
    schedule = StrategySchedule(
        interval_minutes=3, task_type=ORDER_WATCH_TASK_TYPE
    )

    assert schedule.interval_minutes == 3
    with pytest.raises(ValueError, match="interval_minutes must be >= 3"):
        StrategySchedule(interval_minutes=2, task_type=ORDER_WATCH_TASK_TYPE)


def test_an_unknown_kind_is_refused_rather_than_defaulted() -> None:
    with pytest.raises(ValueError, match="unsupported schedule task_type"):
        StrategySchedule(interval_minutes=20, task_type="something_else")


def test_the_watch_runs_to_the_close_and_the_analysis_stops_short_of_it() -> None:
    """An analysis run takes minutes; a watch is two tool calls."""

    analysis = derive_intraday_schedule_times(30, MARKET_ANALYSIS_TASK_TYPE)
    watch = derive_intraday_schedule_times(30, ORDER_WATCH_TASK_TYPE)

    assert analysis[0] == "09:30"
    assert analysis[-1] == "14:30"
    assert watch[-1] == "15:00"
    # And the watch covers the last half hour of the morning session too.
    assert "11:30" in watch
    assert "11:30" not in analysis


def test_a_three_minute_watch_covers_both_sessions_without_the_break() -> None:
    times = derive_intraday_schedule_times(3, ORDER_WATCH_TASK_TYPE)

    assert len(times) == 82
    assert times[0] == "09:30"
    assert times[-1] == "15:00"
    assert not [value for value in times if "11:33" <= value <= "12:57"]


def test_the_times_cap_leaves_room_for_the_faster_kind() -> None:
    """48 was sized for a 15-minute cadence and would reject a 3-minute one."""

    assert cadence_for(ORDER_WATCH_TASK_TYPE).max_schedule_times >= 82
    assert cadence_for(MARKET_ANALYSIS_TASK_TYPE).max_schedule_times == 48


def test_updating_a_schedule_is_held_to_the_same_floor_as_creating_one() -> None:
    """apply_update bypasses __post_init__, so it has to check for itself."""

    schedule = StrategySchedule(interval_minutes=20)

    with pytest.raises(ValueError, match="interval_minutes must be >= 15"):
        schedule.apply_update(enabled=True, interval_minutes=3)

    assert schedule.interval_minutes == 20


def test_an_order_watch_keeps_its_own_floor_on_update() -> None:
    schedule = StrategySchedule(interval_minutes=5, task_type=ORDER_WATCH_TASK_TYPE)

    schedule.apply_update(enabled=True, interval_minutes=3)

    assert schedule.interval_minutes == 3
    assert schedule.schedule_times[-1] == "15:00"
