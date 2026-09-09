from typing import Annotated

from fastapi import APIRouter, Depends

from backend.api.deps import get_system_status_service
from backend.api.schemas.error import error_responses
from backend.api.schemas.system_status import SystemStatusResponse
from backend.api.security import require_authenticated
from backend.business.system_status import SystemStatusService

router = APIRouter(
    prefix="/api/aniu/system-status",
    tags=["SystemStatus"],
    dependencies=[Depends(require_authenticated)],
    responses=error_responses(401, 403),
)


@router.get("", response_model=SystemStatusResponse)
async def get_system_status(
    service: Annotated[SystemStatusService, Depends(get_system_status_service)],
) -> object:
    return await service.overview()
