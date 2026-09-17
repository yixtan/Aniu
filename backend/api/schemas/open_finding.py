"""HTTP contract for unanswered objections."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

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
    dispositions: list[DispositionResponse] = []
    created_at: datetime
    closed_at: datetime | None = None


class RaiseFindingRequest(ApiModel):
    finding: str = Field(min_length=1, max_length=2000)
    # Required, and not merely non-empty by convention: an objection nobody
    # can settle is answered in every future run and never leaves the list.
    resolution_test: str = Field(min_length=1, max_length=2000)
    evaluation_id: int | None = None


__all__ = [
    "DispositionResponse",
    "OpenFindingResponse",
    "RaiseFindingRequest",
]
