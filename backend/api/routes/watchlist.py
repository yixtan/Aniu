from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status

from backend.api.deps import get_watchlist_service
from backend.api.schemas.error import error_responses
from backend.api.schemas.watchlist import (
    AddWatchlistItemRequest,
    WatchlistItemResponse,
    WatchlistResponse,
)
from backend.api.security import require_authenticated
from backend.business.watchlist import WatchlistService
from backend.business.watchlist.service import (
    StockNameUnavailableError,
    WatchlistAlreadyFollowedError,
    WatchlistFullError,
)

router = APIRouter(
    prefix="/api/aniu/watchlist",
    tags=["Watchlist"],
    dependencies=[Depends(require_authenticated)],
    responses=error_responses(401, 403, 422),
)


@router.get("", response_model=WatchlistResponse)
async def list_watchlist(
    service: Annotated[WatchlistService, Depends(get_watchlist_service)],
) -> object:
    return {"items": await service.list_items()}


@router.post(
    "",
    response_model=WatchlistItemResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(409, 502),
)
async def add_watchlist_item(
    service: Annotated[WatchlistService, Depends(get_watchlist_service)],
    payload: AddWatchlistItemRequest = Body(...),
) -> object:
    try:
        return await service.add(payload.symbol)
    except (WatchlistAlreadyFollowedError, WatchlistFullError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except StockNameUnavailableError as exc:
        # The quote provider, not the request, is what failed here.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist_item(
    item_id: int,
    service: Annotated[WatchlistService, Depends(get_watchlist_service)],
) -> None:
    if not await service.delete(item_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "关注记录不存在")
