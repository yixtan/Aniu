"""Declare today's cap, and read what was declared before."""

from __future__ import annotations

from backend.business.exposure.models import ExposureCap
from backend.business.exposure.ports import ExposureCapRepositoryPort
from backend.business.shared import CommitterPort


class ExposureCapService:
    def __init__(
        self,
        repository: ExposureCapRepositoryPort,
        *,
        committer: CommitterPort | None = None,
    ) -> None:
        self._repository = repository
        self._committer = committer

    async def declare(self, cap: ExposureCap) -> ExposureCap:
        stored = await self._repository.declare(cap)
        if self._committer is not None:
            await self._committer.commit()
        return stored

    async def latest(self) -> ExposureCap | None:
        return await self._repository.latest()

    async def for_run(self, run_id: int) -> ExposureCap | None:
        return await self._repository.for_run(run_id)

    async def recent(self, *, limit: int = 10) -> list[ExposureCap]:
        return await self._repository.recent(limit=limit)


__all__ = ["ExposureCapService"]
