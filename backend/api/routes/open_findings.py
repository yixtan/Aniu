"""Raise an objection, see the open ones, close one."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.deps import get_open_finding_service
from backend.api.schemas.error import error_responses
from backend.api.schemas.open_finding import (
    OpenFindingResponse,
    RaiseFindingRequest,
)
from backend.api.security import require_authenticated
from backend.business.open_findings import OpenFindingService

router = APIRouter(
    prefix="/api/aniu/open-findings",
    tags=["OpenFindings"],
    dependencies=[Depends(require_authenticated)],
    responses=error_responses(401, 403, 404),
)


@router.post(
    "",
    response_model=OpenFindingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def raise_finding(
    payload: RaiseFindingRequest,
    service: Annotated[OpenFindingService, Depends(get_open_finding_service)],
) -> object:
    return await service.raise_finding(
        finding=payload.finding,
        resolution_test=payload.resolution_test,
        evaluation_id=payload.evaluation_id,
    )


@router.get("", response_model=list[OpenFindingResponse])
async def list_findings(
    service: Annotated[OpenFindingService, Depends(get_open_finding_service)],
) -> object:
    return await service.list_all()


@router.post("/{finding_id}/close", response_model=OpenFindingResponse)
async def close_finding(
    finding_id: int,
    service: Annotated[OpenFindingService, Depends(get_open_finding_service)],
) -> object:
    """Only reachable by a person. A run may say where it stands, not that
    the matter is settled."""

    closed = await service.close(finding_id)
    if closed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no such finding",
        )
    return closed
