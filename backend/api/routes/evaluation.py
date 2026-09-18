"""Request an independent review of a finished run, and read the last one."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.deps import ApiRuntimePort, get_evaluation_service, get_runtime
from backend.api.schemas.error import error_responses
from backend.api.schemas.evaluation import (
    EvaluationResponse,
    RequestEvaluationBody,
)
from backend.api.security import require_authenticated
from backend.business.evaluations import EvaluationService

router = APIRouter(
    prefix="/api/aniu/runs/{run_id}/evaluation",
    tags=["Runs"],
    dependencies=[Depends(require_authenticated)],
    responses=error_responses(401, 403, 404),
)


@router.post("", response_model=EvaluationResponse, status_code=status.HTTP_201_CREATED)
async def request_evaluation(
    run_id: int,
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
    runtime: Annotated[ApiRuntimePort, Depends(get_runtime)],
    payload: RequestEvaluationBody | None = None,
) -> object:
    """Queue a review, optionally with a question of the operator's own.

    Optional body, so the button alone still works and nothing that called
    this before has to start sending one.
    """

    payload = payload or RequestEvaluationBody()

    evaluation = await service.request(
        run_id, operator_question=payload.operator_question
    )
    worker = runtime.evaluation_worker
    if worker is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="evaluation worker is not running",
        )
    await worker.submit(evaluation.evaluation_id)
    return evaluation


@router.get("", response_model=EvaluationResponse)
async def read_latest_evaluation(
    run_id: int,
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> object:
    evaluation = await service.latest_for_run(run_id)
    if evaluation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no evaluation for this run",
        )
    return evaluation
