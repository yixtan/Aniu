"""Ports for unanswered objections."""

from __future__ import annotations

from typing import Protocol

from backend.business.open_findings.models import OpenFinding


class OpenFindingRepositoryPort(Protocol):
    async def add(self, finding: OpenFinding) -> OpenFinding: ...

    async def get_by_id(self, finding_id: int) -> OpenFinding | None: ...

    async def list_open(self, *, limit: int) -> list[OpenFinding]: ...

    async def list_all(self) -> list[OpenFinding]: ...

    async def save(self, finding: OpenFinding) -> OpenFinding: ...


class OpenFindingsPort(Protocol):
    """What a run needs: the objections it has to answer this time."""

    async def current(self) -> tuple[OpenFinding, ...]: ...
