"""Raise an objection, answer one, close one."""

from __future__ import annotations

from backend.business.open_findings.models import (
    MAX_OPEN_FINDINGS,
    OpenFinding,
    Verdict,
)
from backend.business.open_findings.ports import OpenFindingRepositoryPort
from backend.business.shared import CommitterPort


class OpenFindingService:
    def __init__(
        self,
        repository: OpenFindingRepositoryPort,
        *,
        committer: CommitterPort | None = None,
    ) -> None:
        self._repository = repository
        self._committer = committer

    async def raise_finding(
        self,
        *,
        finding: str,
        resolution_test: str,
        evaluation_id: int | None = None,
    ) -> OpenFinding:
        stored = await self._repository.add(
            OpenFinding(
                finding=finding,
                resolution_test=resolution_test,
                evaluation_id=evaluation_id,
            )
        )
        await self._commit()
        return stored

    async def current(self) -> tuple[OpenFinding, ...]:
        """The objections a run must answer, newest first and capped."""

        return tuple(await self._repository.list_open(limit=MAX_OPEN_FINDINGS))

    async def list_all(self) -> list[OpenFinding]:
        return await self._repository.list_all()

    async def dispose(
        self,
        finding_id: int,
        *,
        run_id: int,
        verdict: Verdict,
        note: str,
    ) -> OpenFinding | None:
        finding = await self._repository.get_by_id(finding_id)
        if finding is None:
            return None
        finding.dispose(run_id=run_id, verdict=verdict, note=note)
        stored = await self._repository.save(finding)
        await self._commit()
        return stored

    async def close(self, finding_id: int, *, note: str) -> OpenFinding | None:
        """Only a person gets here.

        A run may state where it stands — including that it believes the
        resolution test is met — but letting it also decide the objection is
        answered would let it write "经复核，该顾虑不成立" and move on, which is
        exactly what the memory it writes for itself already does.
        """

        finding = await self._repository.get_by_id(finding_id)
        if finding is None:
            return None
        finding.close(note)
        stored = await self._repository.save(finding)
        await self._commit()
        return stored

    async def _commit(self) -> None:
        if self._committer is not None:
            await self._committer.commit()


__all__ = ["OpenFindingService"]
