"""Request an evaluation, and run one."""

from __future__ import annotations

import logging

from backend.business.evaluations.models import Evaluation, EvaluationStatus
from backend.business.evaluations.ports import (
    EvaluationRepositoryPort,
    EvaluatorPort,
)
from backend.business.runs.numbering import is_order_watch_task
from backend.business.shared import CommitterPort, DomainError

logger = logging.getLogger(__name__)


class EvaluationNotApplicableError(DomainError):
    """A review was asked of a run it cannot say anything about.

    An order watch is the one such run. The review asks about investment
    judgement, and a watch forms none: it checks whether conditions an
    analysis wrote have triggered and carries out what was written. It also
    leaves nothing to review from. The reviewer reads the Run stage's report,
    which a watch does not have, and this run's own orders, which counts
    placing and not cancelling, the one thing a watch does. What a review
    drafts becomes an open finding that every analysis must answer, so a
    review of a watch would hand the analyses a question about work they did
    not do. None of the eleven reviews made by 2026-09-29 was of a watch.
    """

    def __init__(self, run_id: int) -> None:
        super().__init__(
            "盯盘记录不做独立评估：盯盘只按操盘写好的清单核对执行，"
            "没有投资判断可审。请对操盘记录发起评估。"
        )
        self.run_id = run_id


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

    async def request(
        self, run_id: int, *, operator_question: str = ""
    ) -> Evaluation:
        if is_order_watch_task(run_id):
            raise EvaluationNotApplicableError(run_id)
        evaluation = await self._repository.create(
            run_id, operator_question=operator_question
        )
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
            result = await self._evaluator.evaluate(
                evaluation.run_id,
                operator_question=evaluation.operator_question,
            )
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

    async def settle_orphans(self) -> int:
        """Fail reviews a previous process was running when it went away.

        The worker's queue is in memory and losing a queued request costs a
        button press, which is why it has no startup sweep of its own. The row
        already marked RUNNING is a different matter: nothing will ever pick it
        up again, and the card polls a PENDING or RUNNING evaluation on a timer
        — so without this it spins on 「正在生成提问与回答，通常一到两分钟」
        until someone looks in the database.
        """

        settled = 0
        for evaluation in await self._repository.list_running():
            evaluation.fail("评估被进程重启打断，未完成；请重新发起。")
            await self._repository.save(evaluation)
            settled += 1
        if settled:
            await self._commit()
            logger.info("settled orphan evaluations", extra={"count": settled})
        return settled

    async def latest_for_run(self, run_id: int) -> Evaluation | None:
        return await self._repository.latest_for_run(run_id)

    async def _commit(self) -> None:
        if self._committer is not None:
            await self._committer.commit()


__all__ = ["EvaluationNotApplicableError", "EvaluationService"]
