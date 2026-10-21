"""The account's return over time, beside the benchmark."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from backend.api.deps import get_performance_service
from backend.api.schemas.error import error_responses
from backend.api.schemas.performance import ReturnCurveResponse
from backend.api.security import require_authenticated
from backend.business.performance import PerformanceService, ReturnCurveDTO

router = APIRouter(
    prefix="/api/aniu/performance",
    tags=["Performance"],
    dependencies=[Depends(require_authenticated)],
    responses=error_responses(401, 403),
)


@router.get("/return-curve", response_model=ReturnCurveResponse)
async def get_return_curve(
    service: Annotated[PerformanceService, Depends(get_performance_service)],
) -> ReturnCurveDTO:
    return await service.return_curve()
