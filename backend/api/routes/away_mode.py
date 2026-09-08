from typing import Annotated

from fastapi import APIRouter, Body, Depends

from backend.api.deps import get_away_mode_service
from backend.api.schemas.away_mode import AwayModeResponse, SetAwayModeRequest
from backend.api.schemas.error import error_responses
from backend.api.security import require_authenticated
from backend.business.away import AwayModeService

router = APIRouter(
    prefix="/api/aniu/away-mode",
    tags=["Away mode"],
    dependencies=[Depends(require_authenticated)],
    responses=error_responses(401, 403, 422),
)


@router.get("", response_model=AwayModeResponse)
async def get_away_mode(
    service: Annotated[AwayModeService, Depends(get_away_mode_service)],
) -> object:
    return await service.get_state()


@router.put("", response_model=AwayModeResponse)
async def set_away_mode(
    service: Annotated[AwayModeService, Depends(get_away_mode_service)],
    payload: SetAwayModeRequest = Body(...),
) -> object:
    return await service.set_enabled(payload.enabled)
