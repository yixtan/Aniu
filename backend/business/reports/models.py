"""Domain models for emailing a completed run report."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from backend.business.shared.trading import utc_now

MAX_ADDRESS_LENGTH = 254
MAX_SUBJECT_LENGTH = 200
# Deliberately permissive: the mail provider is the real authority on what it
# accepts, and a strict local pattern only rejects valid unusual addresses.
_ADDRESS = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


def normalize_address(value: str, field_name: str) -> str:
    address = value.strip()
    if not address:
        raise ValueError(f"{field_name} must not be empty")
    if len(address) > MAX_ADDRESS_LENGTH:
        raise ValueError(
            f"{field_name} must be at most {MAX_ADDRESS_LENGTH} characters"
        )
    if not _ADDRESS.fullmatch(address):
        raise ValueError(f"{field_name} must be an email address")
    return address


@dataclass(frozen=True, slots=True)
class EmailDeliverySettings:
    """Where run reports are mailed, and whether that is switched on.

    The provider API key is never held here; it lives in the encrypted secret
    store and is resolved only when a message is actually sent.
    """

    sender: str
    recipient: str
    enabled: bool = True
    api_key_last_four: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sender", normalize_address(self.sender, "sender"))
        object.__setattr__(
            self, "recipient", normalize_address(self.recipient, "recipient")
        )

    @property
    def api_key_configured(self) -> bool:
        return bool(self.api_key_last_four)


@dataclass(frozen=True, slots=True)
class RunReportMail:
    """One rendered report ready to be handed to the mail provider."""

    run_id: int
    subject: str
    html: str

    def __post_init__(self) -> None:
        if self.run_id <= 0:
            raise ValueError("run_id must be positive")
        if not self.html.strip():
            raise ValueError("report html must not be empty")
        subject = self.subject.strip()
        if not subject:
            raise ValueError("subject must not be empty")
        object.__setattr__(self, "subject", subject[:MAX_SUBJECT_LENGTH])


__all__ = [
    "MAX_ADDRESS_LENGTH",
    "MAX_SUBJECT_LENGTH",
    "EmailDeliverySettings",
    "RunReportMail",
    "normalize_address",
]
