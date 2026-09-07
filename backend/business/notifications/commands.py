"""Notification channel commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.business.notifications.models import NotificationChannelKind

UNSET: object = object()


@dataclass(frozen=True, slots=True)
class CreateChannelCommand:
    """Create one push channel. ``secret`` is the URL or vendor key."""

    name: str
    kind: NotificationChannelKind
    secret: str
    enabled: bool = True
    subscribed_events: Any = None
    body_template: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateChannelCommand:
    """Partial channel update. Use UNSET for omitted fields.

    An omitted or blank ``secret`` keeps the stored one, matching how model
    channel API keys behave.
    """

    channel_id: int
    name: Any = field(default=UNSET)
    enabled: Any = field(default=UNSET)
    subscribed_events: Any = field(default=UNSET)
    body_template: Any = field(default=UNSET)
    secret: Any = field(default=UNSET)

    def provided(self, name: str) -> bool:
        return getattr(self, name) is not UNSET


__all__ = ["UNSET", "CreateChannelCommand", "UpdateChannelCommand"]
