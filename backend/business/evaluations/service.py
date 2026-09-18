"""Request an evaluation, and run one."""

from __future__ import annotations

import logging

from backend.business.evaluations.models import Evaluation, EvaluationStatus
from backend.business.evaluations.ports import (
    EvaluationRepositoryPort,
    EvaluatorPort,
)
from backend.business.shared import CommitterPort

logger = logging.getLogger(__name__)


class EvaluationService:
    """The whole lane, minus the queue that feeds it.

    Nothing here claims a run job or touches `active_guard`. That is the point
    of the lane: submitting to `run_jobs` takes the exclusive lock at creation
    time, not at execution time, so an evaluation queued while an analysis is
    running would not wait behind it — it would be refused outright.
    """

    def __init__(
        self,
        repository: EvaluationRepositoryPort,
        evaluator: EvaluatorPort | None = None,
        *,
        committer: CommitterPort | None = None,
    ) -> None:
        self._repository = repository
        self._evaluator = evaluator
        self._committer = committer

    async def request(self, run_id: int) -> Evaluation:
        evaluation = await self._repository.create(run_id)
        await self._commit()
        return evaluation

    async def execute(self, evaluation_id: int) -> Evaluation | None:
        if self._evaluator is None:
            raise RuntimeError("evaluator is not configured")
        evaluation = await self._repository.get_by_id(evaluation_id)
        if evaluation is None or evaluation.status is not EvaluationStatus.PENDING:
            return evaluation
        evaluation.start()
        await self._repository.save(evaluation)
        await self._commit()
        try:
            result = await self._evaluator.evaluate(evaluation.run_id)
        except Exception as exc:  # noqa: BLE001 - the failure is the record
            logger.warning(
                "run evaluation failed",
                extra={"run_id": evaluation.run_id},
                exc_info=True,
            )
            evaluation.fail(str(exc))
        else:
            evaluation.complete(
                questions=result.questions,
                answers=result.answers,
                digest=result.digest,
                candidates=result.candidates,
                total_tokens=result.total_tokens,
                cached_tokens=result.cached_tokens,
            )
        stored = await self._repository.save(evaluation)
        await self._commit()
        return stored

    async def latest_for_run(self, run_id: int) -> Evaluation | None:
        return await self._repository.latest_for_run(run_id)

    async def _commit(self) -> None:
        if self._committer is not None:
            await self._committer.commit()


__all__ = ["EvaluationService"]
