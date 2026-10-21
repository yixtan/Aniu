"""Ports for the daily account value and what it is compared with."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Protocol

from backend.business.performance.models import (
    AccountStart,
    DailyEquity,
    DayFill,
    EquityRecordingStatus,
)


class DailyEquityRecorderPort(Protocol):
    """Records and commits independently of the account cache transaction."""

    async def record_day(self, equity: DailyEquity) -> None: ...


class EquityRecordingStatusPort(Protocol):
    async def recording_status(self) -> EquityRecordingStatus | None: ...


class DailyEquityRepositoryPort(Protocol):
    """Transaction-scoped repository; its caller owns commit or rollback."""

    async def record_day(self, equity: DailyEquity) -> None: ...

    async def list_days(self) -> list[DailyEquity]: ...


class BenchmarkCloseRepositoryPort(Protocol):
    async def closes(self, symbol: str) -> dict[date, float]: ...

    async def save_closes(self, symbol: str, closes: Mapping[date, float]) -> None: ...


class BenchmarkHistoryPort(Protocol):
    async def daily_closes(
        self, symbol: str, *, start: date, end: date
    ) -> dict[date, float]: ...


class AccountFactsPort(Protocol):
    """What the account itself says about where the curve starts and moves."""

    async def account_start(self) -> AccountStart | None: ...

    async def day_fills(self) -> list[DayFill]: ...


class TradingCalendarPort(Protocol):
    def last_trading_day_on_or_before(self, day: date) -> date: ...
