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


def task_type_of(task_id: int) -> int:
    """The type digit of a task id, or 0 for ids from before task numbering."""

    text = str(task_id)
    return int(text[8]) if len(text) > 8 and text[8].isdigit() else 0


def is_order_watch_task(task_id: int) -> bool:
    return task_type_of(task_id) == ORDER_WATCH_TASK_TYPE


def run_id_prefix(reference_date: date, *, task_type: int = RUN_TASK_TYPE) -> str:
    if task_type < 0 or task_type > 9:
        raise ValueError("task_type must be a single digit integer")
    return f"{reference_date:%Y%m%d}{task_type}"
