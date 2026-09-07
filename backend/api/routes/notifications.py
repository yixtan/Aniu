from typing import Annotated

from fastapi import APIRouter, Body, Depends, status

from backend.api.deps import get_notification_service
from backend.api.schemas.error import error_responses
from backend.api.schemas.notification import (
    CreateNotificationChannelRequest,
    NotificationChannelResponse,
    NotificationTestResultResponse,
    UpdateNotificationChannelRequest,
)
from backend.api.security import require_authenticated
from backend.business.notifications import (
    CreateChannelCommand,
    NotificationChannelKind,
    NotificationService,
    UpdateChannelCommand,
)
from backend.business.notifications.commands import UNSET

router = APIRouter(
    prefix="/api/aniu/notifications",
    tags=["Notifications"],
    dependencies=[Depends(require_authenticated)],
    responses=error_responses(401, 403, 422),
)


@router.get("/channels", response_model=list[NotificationChannelResponse])
async def list_notification_channels(
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> object:
    return await service.list_channels()


@router.post(
    "/channels",
    status_code=status.HTTP_201_CREATED,
    response_model=NotificationChannelResponse,
)
async def create_notification_channel(
    service: Annotated[NotificationService, Depends(get_notification_service)],
    payload: CreateNotificationChannelRequest = Body(...),
) -> object:
    return await service.create_channel(
        CreateChannelCommand(
            name=payload.name,
            kind=NotificationChannelKind(payload.kind),
            secret=payload.secret,
            enabled=payload.enabled,
            subscribed_events=payload.subscribed_events,
            body_template=payload.body_template,
        )
    )


@router.put(
    "/channels/{channel_id}",
    response_model=NotificationChannelResponse,
    responses=error_responses(404),
)
async def update_notification_channel(
    channel_id: int,
    service: Annotated[NotificationService, Depends(get_notification_service)],
    payload: UpdateNotificationChannelRequest = Body(...),
) -> object:
    fields = payload.model_dump(exclude_unset=True)
    return await service.update_channel(
        UpdateChannelCommand(
            channel_id=channel_id,
            name=fields.get("name", UNSET),
            enabled=fields.get("enabled", UNSET),
            subscribed_events=fields.get("subscribed_events", UNSET),
            body_template=fields.get("body_template", UNSET),
            secret=fields.get("secret", UNSET),
        )
    )


@router.delete(
    "/channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(404),
)
async def delete_notification_channel(
    channel_id: int,
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> None:
    await service.delete_channel(channel_id)


@router.post(
    "/channels/{channel_id}/test",
    response_model=NotificationTestResultResponse,
    responses=error_responses(404),
)
async def test_notification_channel(
    channel_id: int,
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> object:
    return await service.send_test(channel_id)
