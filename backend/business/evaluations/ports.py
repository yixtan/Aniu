"""Ports for the evaluation lane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.business.evaluations.models import Evaluation, FindingCandidate


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """What one review produced, and what the provider billed for it."""

    questions: str
    answers: str
    candidates: tuple[FindingCandidate, ...] = ()
    total_tokens: int = 0
    cached_tokens: int = 0


class EvaluationRepositoryPort(Protocol):
    async def create(self, run_id: int) -> Evaluation: ...

    async def get_by_id(self, evaluation_id: int) -> Evaluation | None: ...

    async def latest_for_run(self, run_id: int) -> Evaluation | None: ...

    async def save(self, evaluation: Evaluation) -> Evaluation: ...


class EvaluatorPort(Protocol):
    async def evaluate(self, run_id: int) -> EvaluationResult: ...


__all__ = ["EvaluationRepositoryPort", "EvaluationResult", "EvaluatorPort"]
