"""HTTP contract for unanswered objections."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from backend.api.schemas.common import ApiModel


class DispositionResponse(ApiModel):
    run_id: int
    verdict: str
    note: str
    at: datetime


class OpenFindingResponse(ApiModel):
    finding_id: int
    evaluation_id: int | None = None
    finding: str
    resolution_test: str
    status: str
    times_disputed: int = 0
    # Whether the newest disposition says the test is met, so the page can
    # show which findings are waiting on the operator rather than on a run.
    settlement_proposed: bool = False
    dispositions: list[DispositionResponse] = []
    created_at: datetime
    closed_at: datetime | None = None
    closing_outcome: str = ""
    closing_note: str = ""

    @field_validator("closing_outcome", mode="before")
    @classmethod
    def _unrecorded_reads_as_empty(cls, value: object) -> object:
        # Never null, so the page can compare it without a guard. An open
        # finding and one closed before the two endings were told apart both
        # arrive here as None, and both mean the same thing: not recorded.
        return "" if value is None else value


class RaiseFindingRequest(ApiModel):
    finding: str = Field(min_length=1, max_length=2000)
    # Required, and not merely non-empty by convention: an objection nobody
    # can settle is answered in every future run and never leaves the list.
    resolution_test: str = Field(min_length=1, max_length=2000)
    evaluation_id: int | None = None


class CloseFindingRequest(ApiModel):
    # Which of the two endings, and why. Both are required for the same reason
    # the resolution test is: without them, a finding settled by evidence and
    # one abandoned as the wrong question are the same row, and a month later
    # nobody can tell them apart.
    outcome: Literal["MET", "WITHDRAWN"]
    note: str = Field(min_length=1, max_length=2000)


__all__ = [
    "CloseFindingRequest",
    "DispositionResponse",
    "OpenFindingResponse",
    "RaiseFindingRequest",
]
