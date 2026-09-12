"""Run/task numbering helpers."""

from __future__ import annotations

from datetime import date

RUN_TASK_TYPE = 1
SCHEDULE_TASK_TYPE = 2
# Order-watch runs are numbered apart from analysis runs so the ninth digit of
# a task id still says who acted — the same thing `is_dream_task` reads. They
# will outnumber analysis runs roughly five to one, and folded together they
# would drown the run counts and the token chart.
ORDER_WATCH_TASK_TYPE = 3


def build_run_id(
    reference_date: date,
    *,
    sequence: int,
    task_type: int = RUN_TASK_TYPE,
) -> int:
    if sequence <= 0:
        raise ValueError("sequence must be greater than 0")
    if task_type < 0 or task_type > 9:
        raise ValueError("task_type must be a single digit integer")
    return int(f"{reference_date:%Y%m%d}{task_type}{sequence:02d}")


def run_id_prefix(reference_date: date, *, task_type: int = RUN_TASK_TYPE) -> str:
    if task_type < 0 or task_type > 9:
        raise ValueError("task_type must be a single digit integer")
    return f"{reference_date:%Y%m%d}{task_type}"
