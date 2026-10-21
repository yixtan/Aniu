"""Ports for the daily account value."""

from __future__ import annotations

from typing import Protocol

from backend.business.performance.models import DailyEquity


class DailyEquityRecorderPort(Protocol):
    async def record_day(self, equity: DailyEquity) -> None: ...


class DailyEquityRepositoryPort(DailyEquityRecorderPort, Protocol):
    async def list_days(self) -> list[DailyEquity]: ...
