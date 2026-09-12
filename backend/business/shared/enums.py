"""Shared domain enums."""

from enum import StrEnum


class TriggerSource(StrEnum):
    """Supported strategy run trigger sources."""

    MANUAL = "manual"
    SCHEDULED = "scheduled"


class RunStatus(StrEnum):
    """Strategy run statuses."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class RunState(StrEnum):
    """FSM states for a run.

    `RUN` and `SUMMARY` are the analysis run's two stages. `WATCH` is an order
    watch, which is one stage on its own: it acts on a plan an analysis run
    already wrote, and has no report to render afterwards.
    """

    RUN = "Run"
    SUMMARY = "Summary"
    WATCH = "Watch"
    COMPLETED = "Completed"
    FAILED = "Failed"
