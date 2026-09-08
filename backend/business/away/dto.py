"""Presentation shapes for away mode."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from backend.business.away.models import AwayMode


@dataclass(frozen=True, slots=True)
class AwayModeDTO:
    enabled: bool
    active_date: date | None
    updated_at: datetime


def to_away_mode_dto(mode: AwayMode, *, moment: datetime) -> AwayModeDTO:
    """Report the mode as of ``moment``.

    ``enabled`` is derived rather than stored, so a client that asks the day
    after it was switched on is told it is off without anything having had to
    switch it off.
    """

    return AwayModeDTO(
        enabled=mode.is_active(moment),
        active_date=mode.active_date,
        updated_at=mode.updated_at,
    )


__all__ = ["AwayModeDTO", "to_away_mode_dto"]
