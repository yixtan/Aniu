"""Read models for run report email delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.business.reports.models import EmailDeliverySettings


@dataclass(frozen=True, slots=True)
class EmailDeliverySettingsDTO:
    configured: bool
    enabled: bool
    sender: str
    recipient: str
    api_key_configured: bool
    api_key_last_four: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReportMailResultDTO:
    run_id: int
    delivered: bool
    message: str


UNCONFIGURED = EmailDeliverySettingsDTO(
    configured=False,
    enabled=False,
    sender="",
    recipient="",
    api_key_configured=False,
    api_key_last_four=None,
    created_at=None,
    updated_at=None,
)


def to_email_settings_dto(
    settings: EmailDeliverySettings | None,
) -> EmailDeliverySettingsDTO:
    if settings is None:
        return UNCONFIGURED
    return EmailDeliverySettingsDTO(
        configured=True,
        enabled=settings.enabled,
        sender=settings.sender,
        recipient=settings.recipient,
        api_key_configured=settings.api_key_configured,
        api_key_last_four=settings.api_key_last_four,
        created_at=settings.created_at,
        updated_at=settings.updated_at,
    )


__all__ = [
    "UNCONFIGURED",
    "EmailDeliverySettingsDTO",
    "ReportMailResultDTO",
    "to_email_settings_dto",
]
