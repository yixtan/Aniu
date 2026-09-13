"""Schedule timing helpers and the persisted schedule entity."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from backend.business.settings.prompt import utc_now
from backend.business.shared.trading.value_objects import ensure_positive_int

MARKET_ANALYSIS_TASK_TYPE = "market_analysis"
ORDER_WATCH_TASK_TYPE = "order_watch"
MORNING_START_MINUTES = 9 * 60 + 30
# The latest an analysis run may start: ten minutes before the bell. A run
# normally thinks for two to three minutes, so with ten left it can still
# trade; the few that run longer (the longest of the last fourteen days was
# 753s) meet a closed market and `trade` refuses — that run's analysis is
# wasted, and nothing else happens. Jeffrey chose this margin on 2026-09-13.
MORNING_END_MINUTES = 11 * 60 + 20
AFTERNOON_START_MINUTES = 13 * 60
AFTERNOON_END_MINUTES = 14 * 60 + 50

_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
MAX_CUSTOM_SCHEDULE_TIMES = 48

# An analysis schedule is chosen from a fixed set of intervals, and each one
# has a fixed timetable, written out rather than derived. The rule the rows
# obey (and a test re-derives) is: start at the open, step by the interval, and
# add one last run at the latest safe start whenever the grid would otherwise
# leave fifteen minutes or more before the latest start uncovered. Writing the rows out
# is what makes them reviewable one by one; the rule is what makes them
# defensible. 15 and 30 already landed on the old end; 20 lost a run each
# session because ninety minutes is not a multiple of twenty.
ANALYSIS_INTERVAL_CHOICES: tuple[int, ...] = (15, 20, 25, 30, 35, 40, 45, 50, 55, 60)
ANALYSIS_TIMETABLE: dict[int, tuple[str, ...]] = {
    15: ("09:30", "09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15",
         "13:00", "13:15", "13:30", "13:45", "14:00", "14:15", "14:30", "14:45"),
    20: ("09:30", "09:50", "10:10", "10:30", "10:50", "11:10",
         "13:00", "13:20", "13:40", "14:00", "14:20", "14:40"),
    25: ("09:30", "09:55", "10:20", "10:45", "11:10",
         "13:00", "13:25", "13:50", "14:15", "14:40"),
    30: ("09:30", "10:00", "10:30", "11:00", "11:20",
         "13:00", "13:30", "14:00", "14:30", "14:50"),
    35: ("09:30", "10:05", "10:40", "11:15",
         "13:00", "13:35", "14:10", "14:45"),
    40: ("09:30", "10:10", "10:50", "11:20",
         "13:00", "13:40", "14:20", "14:50"),
    45: ("09:30", "10:15", "11:00", "11:20",
         "13:00", "13:45", "14:30", "14:50"),
    50: ("09:30", "10:20", "11:10",
         "13:00", "13:50", "14:40"),
    55: ("09:30", "10:25", "11:20",
         "13:00", "13:55", "14:50"),
    60: ("09:30", "10:30", "11:20",
         "13:00", "14:00", "14:50"),
}


@dataclass(frozen=True, slots=True)
class TaskCadence:
    """How often a task type may run, and how late it may still start.

    The two windows differ because the tasks do. A market-analysis run takes
    minutes and must not begin one it cannot finish before the bell, so it
    stops well short of the close. An order watch is a couple of tool calls,
    and the close is exactly when it earns its keep — by then the last analysis
    run is already twenty minutes old, and an order left resting has nobody
    else looking at it.
    """

    min_interval_minutes: int
    morning_end_minutes: int
    afternoon_end_minutes: int
    max_schedule_times: int


_CADENCES: dict[str, TaskCadence] = {
    MARKET_ANALYSIS_TASK_TYPE: TaskCadence(
        min_interval_minutes=15,
        morning_end_minutes=MORNING_END_MINUTES,
        afternoon_end_minutes=AFTERNOON_END_MINUTES,
        max_schedule_times=MAX_CUSTOM_SCHEDULE_TIMES,
    ),
    ORDER_WATCH_TASK_TYPE: TaskCadence(
        min_interval_minutes=3,
        morning_end_minutes=11 * 60 + 30,
        afternoon_end_minutes=15 * 60,
        # Two full sessions at the floor interval, with room to spare; the
        # interval, not this, is what actually bounds a derived schedule.
        max_schedule_times=96,
    ),
}
ALLOWED_TASK_TYPES = frozenset(_CADENCES)


def cadence_for(task_type: str) -> TaskCadence:
    try:
        return _CADENCES[task_type]
    except KeyError as error:
        raise ValueError(f"unsupported schedule task_type: {task_type}") from error


def _minutes_to_time_string(total_minutes: int) -> str:
    hour, minute = divmod(total_minutes, 60)
    return f"{hour:02d}:{minute:02d}"


def derive_intraday_schedule_times(
    interval_minutes: int, task_type: str = MARKET_ANALYSIS_TASK_TYPE
) -> tuple[str, ...]:
    """Derive weekday market-session trigger times from one interval."""

    cadence = cadence_for(task_type)
    if interval_minutes < cadence.min_interval_minutes:
        raise ValueError(f"interval_minutes must be >= {cadence.min_interval_minutes}")
    # The fixed timetable wins where one exists. The grid below is kept for the
    # order watch and for a legacy analysis interval saved before the set was
    # fixed — a stored row must never fail to load over a value the API no
    # longer accepts.
    if (
        task_type == MARKET_ANALYSIS_TASK_TYPE
        and interval_minutes in ANALYSIS_TIMETABLE
    ):
        return ANALYSIS_TIMETABLE[interval_minutes]
    times: list[str] = []
    for start, end in (
        (MORNING_START_MINUTES, cadence.morning_end_minutes),
        (AFTERNOON_START_MINUTES, cadence.afternoon_end_minutes),
    ):
        current = start
        while current <= end:
            times.append(_minutes_to_time_string(current))
            current += interval_minutes
    return tuple(times)


def normalize_custom_schedule_times(
    times: tuple[str, ...] | list[str] | None,
    task_type: str = MARKET_ANALYSIS_TASK_TYPE,
) -> tuple[str, ...] | None:
    """Validate and dedupe user-provided HH:MM trigger times (keep order)."""

    if times is None:
        return None
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in times:
        value = str(raw).strip()
        if not _TIME_PATTERN.match(value):
            raise ValueError(f"invalid schedule time: {value}")
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    maximum = cadence_for(task_type).max_schedule_times
    if len(normalized) > maximum:
        raise ValueError(f"too many schedule times: at most {maximum} allowed")
    return tuple(normalized) if normalized else None


@dataclass(slots=True)
class StrategySchedule:
    """Persisted schedule input plus scheduler synchronization state."""

    interval_minutes: int
    enabled: bool = True
    custom_schedule_times: tuple[str, ...] | None = None
    schedule_id: int = 0
    # Defaulted rather than required so every schedule that existed before the
    # column did reads back as what it has always been.
    task_type: str = MARKET_ANALYSIS_TASK_TYPE
    revision: int = 0
    runtime_synced_revision: int = 0
    sync_error: str | None = None
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.schedule_id < 0:
            raise ValueError("schedule_id must be >= 0")
        cadence = cadence_for(self.task_type)
        self.interval_minutes = ensure_positive_int(
            self.interval_minutes, "interval_minutes"
        )
        if self.interval_minutes < cadence.min_interval_minutes:
            raise ValueError(
                f"interval_minutes must be >= {cadence.min_interval_minutes}"
            )
        self.custom_schedule_times = normalize_custom_schedule_times(
            self.custom_schedule_times, self.task_type
        )

    @property
    def schedule_times(self) -> tuple[str, ...]:
        """Effective weekday trigger times (custom when set, else interval-derived)."""

        if self.custom_schedule_times:
            return self.custom_schedule_times
        return derive_intraday_schedule_times(self.interval_minutes, self.task_type)

    def apply_update(
        self,
        *,
        enabled: bool,
        interval_minutes: int,
        custom_schedule_times: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        cadence = cadence_for(self.task_type)
        interval_minutes = ensure_positive_int(interval_minutes, "interval_minutes")
        if interval_minutes < cadence.min_interval_minutes:
            raise ValueError(
                f"interval_minutes must be >= {cadence.min_interval_minutes}"
            )
        self.enabled = enabled
        self.interval_minutes = interval_minutes
        self.custom_schedule_times = normalize_custom_schedule_times(
            custom_schedule_times, self.task_type
        )
        self.revision += 1
        self.sync_error = None
        self.updated_at = utc_now()
        self.__post_init__()
