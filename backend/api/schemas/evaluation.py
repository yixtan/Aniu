"""HTTP contract for independent reviews of a finished run."""

from __future__ import annotations

from datetime import datetime

from backend.api.schemas.common import ApiModel


class EvaluationResponse(ApiModel):
    evaluation_id: int
    run_id: int
    status: str
    questions: str | None = None
    answers: str | None = None
    total_tokens: int = 0
    cached_tokens: int = 0
    failure_reason: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


__all__ = ["EvaluationResponse"]
