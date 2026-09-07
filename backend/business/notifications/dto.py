"""Read models for notification channels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from backend.business.notifications.models import (
    DeliveryStatus,
    NotificationChannel,
    NotificationChannelKind,
    NotificationDelivery,
    NotificationEventKind,
)

_EVENT_ORDER: tuple[NotificationEventKind, ...] = tuple(NotificationEventKind)


def mask_secret(secret: str) -> str:
    """Return a display hint that never reveals a usable credential.

    A webhook URL keeps its scheme and host so the user can tell targets apart;
    vendor keys are reduced to their last four characters.
    """

    value = secret.strip()
    if not value:
        return ""
    tail = value[-4:] if len(value) >= 4 else value
    if "://" in value:
        parsed = urlparse(value)
        if parsed.hostname:
            return f"{parsed.scheme}://{parsed.hostname}/…{tail}"
    return f"…{tail}"


@dataclass(frozen=True, slots=True)
class NotificationChannelDTO:
    id: int
    name: str
    kind: NotificationChannelKind
    enabled: bool
    subscribed_events: tuple[NotificationEventKind, ...]
    body_template: str | None
    target_hint: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NotificationDeliveryDTO:
    id: int
    channel_id: int | None
    channel_name: str
    channel_kind: NotificationChannelKind
    event_kind: NotificationEventKind
    event_label: str
    title: str
    status: DeliveryStatus
    error_message: str | None
    is_test: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NotificationDeliveryPageDTO:
    items: tuple[NotificationDeliveryDTO, ...]
    total: int


def to_notification_delivery_dto(
    delivery: NotificationDelivery,
) -> NotificationDeliveryDTO:
    return NotificationDeliveryDTO(
        id=delivery.id,
        channel_id=delivery.channel_id,
        channel_name=delivery.channel_name,
        channel_kind=delivery.channel_kind,
        event_kind=delivery.event_kind,
        event_label=delivery.event_kind.label,
        title=delivery.title,
        status=delivery.status,
        error_message=delivery.error_message,
        is_test=delivery.is_test,
        created_at=delivery.created_at,
    )


@dataclass(frozen=True, slots=True)
class NotificationTestResultDTO:
    channel_id: int
    delivered: bool
    message: str


def to_notification_channel_dto(
    channel: NotificationChannel,
) -> NotificationChannelDTO:
    return NotificationChannelDTO(
        id=channel.id,
        name=channel.name,
        kind=channel.kind,
        enabled=channel.enabled,
        subscribed_events=tuple(
            event for event in _EVENT_ORDER if event in channel.subscribed_events
        ),
        body_template=channel.body_template,
        target_hint=channel.target_hint,
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    )


__all__ = [
    "NotificationChannelDTO",
    "NotificationDeliveryDTO",
    "NotificationDeliveryPageDTO",
    "NotificationTestResultDTO",
    "mask_secret",
    "to_notification_channel_dto",
    "to_notification_delivery_dto",
]
