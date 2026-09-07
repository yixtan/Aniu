"""Commands for run report email delivery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

UNSET: object = object()


@dataclass(frozen=True, slots=True)
class SaveEmailSettingsCommand:
    """Partial update. An omitted or blank api_key keeps the stored one."""

    sender: Any = field(default=UNSET)
    recipient: Any = field(default=UNSET)
    enabled: Any = field(default=UNSET)
    api_key: Any = field(default=UNSET)

    def provided(self, name: str) -> bool:
        return getattr(self, name) is not UNSET


__all__ = ["UNSET", "SaveEmailSettingsCommand"]
