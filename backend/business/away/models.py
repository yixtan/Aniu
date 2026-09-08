"""Domain model for away mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from backend.business.shared.trading import utc_now

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


def market_date(moment: datetime) -> date:
    """The A-share trading day ``moment`` falls on."""

    return moment.astimezone(MARKET_TIMEZONE).date()


@dataclass(frozen=True, slots=True)
class AwayMode:
    """Which market day away mode is switched on for.

    Held as a date rather than a boolean so that switching off at midnight
    needs no scheduled job: the stored day simply stops matching the current
    one.  A machine asleep across midnight therefore wakes in normal mode,
    which a 00:00 job could not promise — it would not have run.
    """

    active_date: date | None = None
    updated_at: datetime = field(default_factory=utc_now)

    def is_active(self, moment: datetime) -> bool:
        return self.active_date == market_date(moment)


OFF = AwayMode()

__all__ = ["MARKET_TIMEZONE", "OFF", "AwayMode", "market_date"]
