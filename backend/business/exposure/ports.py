"""Ports for the daily exposure cap."""

from __future__ import annotations

from typing import Protocol

from backend.business.exposure.models import ExposureCap


class ExposureCapRepositoryPort(Protocol):
    async def declare(self, cap: ExposureCap) -> ExposureCap: ...

    async def latest(self) -> ExposureCap | None: ...

    async def for_run(self, run_id: int) -> ExposureCap | None: ...

    async def recent(self, *, limit: int) -> list[ExposureCap]: ...


class ExposureCapHistoryPort(Protocol):
    """What a reviewer needs: the series, not today's number."""

    async def recent(self, *, limit: int) -> list[ExposureCap]: ...


class LatestExposureCapPort(Protocol):
    """What a run needs before declaring its own: the one before it."""

    async def latest(self) -> ExposureCap | None: ...


__all__ = [
    "ExposureCapHistoryPort",
    "ExposureCapRepositoryPort",
    "LatestExposureCapPort",
]
