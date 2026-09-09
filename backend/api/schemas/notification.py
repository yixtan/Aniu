from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.api.schemas.common import ApiModel

ChannelKind = Literal["webhook", "serverchan", "wecom_bot", "macos_desktop"]
TradeEvent = Literal[
    "order_placed",
    "order_cancelled",
    "order_filled",
    "run_failed",
    "run_completed",
]
DeliveryStatusLiteral = Literal["delivered", "failed"]


class NotificationChannelResponse(ApiModel):
    id: int
    name: str
    kind: ChannelKind
    enabled: bool
    subscribed_events: list[TradeEvent]
    body_template: str | None
    target_hint: str
    created_at: datetime
    updated_at: datetime


class NotificationTestResultResponse(ApiModel):
    channel_id: int
    delivered: bool
    message: str


class CreateNotificationChannelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    kind: ChannelKind
    secret: str = Field(min_length=1, max_length=2000)
    """Webhook URL for ``webhook``; the sendkey or bot key otherwise."""
    enabled: bool = True
    subscribed_events: list[TradeEvent] = Field(
        default=[
            "order_placed",
            "order_cancelled",
            "order_filled",
            "run_failed",
            "run_completed",
        ],
        min_length=1,
    )
    body_template: str | None = Field(default=None, max_length=4000)


class UpdateNotificationChannelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None
    subscribed_events: list[TradeEvent] | None = Field(default=None, min_length=1)
    body_template: str | None = Field(default=None, max_length=4000)
    secret: str | None = Field(default=None, max_length=2000)
    """Leave empty to keep the stored endpoint; clients never receive it back."""


class NotificationDeliveryResponse(ApiModel):
    id: int
    channel_id: int | None
    channel_name: str
    channel_kind: ChannelKind
    event_kind: TradeEvent
    event_label: str
    title: str
    status: DeliveryStatusLiteral
    error_message: str | None
    is_test: bool
    created_at: datetime


class NotificationDeliveryPageResponse(ApiModel):
    items: list[NotificationDeliveryResponse]
    total: int
