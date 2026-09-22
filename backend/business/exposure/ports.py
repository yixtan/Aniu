"""Ports for the daily exposure cap."""

from __future__ import annotations

from typing import Protocol

from backend.business.exposure.models import ExposureCap


class ExposureCapRepositoryPort(Protocol):
    async def declare(self, cap: ExposureCap) -> ExposureCap: ...

    async def for_run(self, run_id: int) -> ExposureCap | None: ...

    async def recent(self, *, limit: int) -> list[ExposureCap]: ...


class ExposureCapHistoryPort(Protocol):
    """The series, which is the only form either reader is given.

    A reviewer needs it to ask whether the cap responds to anything. A run
    needs it for the same reason turned inward: one row hands the next run a
    number to agree with, a column shows it how long it has been agreeing.

    What differs between the two is not the port but what each is shown of a
    row — the reviewer gets the reasoning, the run gets the number.
    """

    async def recent(self, *, limit: int) -> list[ExposureCap]: ...


__all__ = [
    "ExposureCapHistoryPort",
    "ExposureCapRepositoryPort",
]
