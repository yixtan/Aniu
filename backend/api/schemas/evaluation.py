"""HTTP contract for independent reviews of a finished run."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from backend.api.schemas.common import ApiModel


class FindingCandidateResponse(ApiModel):
    """A finding the review drafted. Raising it is still a person's call."""

    finding: str
    resolution_test: str


class RequestEvaluationBody(ApiModel):
    """What the operator wants asked, if anything. Optional: the button on its
    own is still the ordinary way to run one."""

    operator_question: str = Field(default="", max_length=1000)


class EvaluationResponse(ApiModel):
    evaluation_id: int
    run_id: int
    status: str
    operator_question: str = ""
    questions: str | None = None
    answers: str | None = None
    # The lead, written for someone who does not read the argument.
    digest: str = ""
    candidates: list[FindingCandidateResponse] = []
    total_tokens: int = 0
    cached_tokens: int = 0
    failure_reason: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


__all__ = [
    "EvaluationResponse",
    "FindingCandidateResponse",
    "RequestEvaluationBody",
]
