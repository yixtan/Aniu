"""Deterministic recovery reports; never infer an order from matching prices."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ExecutionResultRow:
    tool_call_id: str
    operation: str
    instruction: str
    outcome: str
    order_id: str | None
    order_status: str | None
    filled_quantity: int | None
    observed_at: datetime | None
    explanation: str


@dataclass(frozen=True)
class ExecutionCheck:
    run_id: int
    run_status: str
    checked_at: datetime
    account_observed_at: datetime | None
    evidence_stale: bool
    interrupted: bool
    unfinished_steps: list[str]
    items: list[ExecutionResultRow]
    notice: str


class ExecutionCheckQueryPort(Protocol):
    async def check(self, run_id: int) -> ExecutionCheck: ...
