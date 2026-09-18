"""Persistence for independent reviews of finished runs."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.business.evaluations.models import (
    Evaluation,
    EvaluationStatus,
    FindingCandidate,
)
from backend.infra.db.models import RunEvaluationModel


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _to_domain(model: RunEvaluationModel) -> Evaluation:
    created = _parse(model.created_at)
    evaluation = Evaluation(
        run_id=int(model.run_id),
        evaluation_id=int(model.id),
        status=EvaluationStatus(model.status),
        operator_question=model.operator_question or "",
        questions=model.questions,
        answers=model.answers,
        digest=model.digest or "",
        candidates=_deserialize_candidates(model.candidates_json),
        total_tokens=int(model.total_tokens or 0),
        cached_tokens=int(model.cached_tokens or 0),
        failure_reason=model.failure_reason,
        started_at=_parse(model.started_at),
        completed_at=_parse(model.completed_at),
    )
    if created is not None:
        evaluation.created_at = created
    return evaluation


def _serialize(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _serialize_candidates(candidates: tuple[FindingCandidate, ...]) -> str | None:
    # Absent rather than "[]", so a review written before drafting existed and
    # one whose drafting produced nothing read the same way — they are the
    # same thing: no drafts to offer.
    if not candidates:
        return None
    return json.dumps(
        [
            {
                "finding": candidate.finding,
                "resolution_test": candidate.resolution_test,
            }
            for candidate in candidates
        ],
        ensure_ascii=False,
    )


def _deserialize_candidates(value: str | None) -> tuple[FindingCandidate, ...]:
    if not value:
        return ()
    try:
        decoded: Any = json.loads(value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(decoded, list):
        return ()
    candidates: list[FindingCandidate] = []
    for entry in decoded:
        if not isinstance(entry, dict):
            continue
        try:
            candidates.append(
                FindingCandidate(
                    finding=str(entry.get("finding", "")),
                    resolution_test=str(entry.get("resolution_test", "")),
                )
            )
        except ValueError:
            continue
    return tuple(candidates)


class RunEvaluationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, run_id: int, *, operator_question: str = "") -> Evaluation:
        # created_at comes from the column default, the way every other table
        # here stamps it — one clock, in UTC, not this process's local zone.
        model = RunEvaluationModel(
            run_id=run_id,
            status=EvaluationStatus.PENDING.value,
            operator_question=operator_question.strip() or None,
        )
        self._session.add(model)
        await self._session.flush()
        return _to_domain(model)

    async def get_by_id(self, evaluation_id: int) -> Evaluation | None:
        model = await self._session.get(RunEvaluationModel, evaluation_id)
        return None if model is None else _to_domain(model)

    async def latest_for_run(self, run_id: int) -> Evaluation | None:
        model = (
            await self._session.scalars(
                select(RunEvaluationModel)
                .where(RunEvaluationModel.run_id == run_id)
                .order_by(RunEvaluationModel.id.desc())
                .limit(1)
            )
        ).first()
        return None if model is None else _to_domain(model)

    async def save(self, evaluation: Evaluation) -> Evaluation:
        model = await self._session.get(RunEvaluationModel, evaluation.evaluation_id)
        if model is None:
            raise ValueError(f"unknown evaluation: {evaluation.evaluation_id}")
        model.status = evaluation.status.value
        model.questions = evaluation.questions
        model.answers = evaluation.answers
        model.digest = evaluation.digest or None
        model.candidates_json = _serialize_candidates(evaluation.candidates)
        model.total_tokens = evaluation.total_tokens
        model.cached_tokens = evaluation.cached_tokens
        model.failure_reason = evaluation.failure_reason
        model.started_at = _serialize(evaluation.started_at)
        model.completed_at = _serialize(evaluation.completed_at)
        await self._session.flush()
        return _to_domain(model)


__all__ = ["RunEvaluationRepository"]
