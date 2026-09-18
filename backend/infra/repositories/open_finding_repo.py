"""Persistence for unanswered objections."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.business.open_findings.models import (
    Disposition,
    FindingStatus,
    OpenFinding,
    Verdict,
)
from backend.infra.db.models import OpenFindingModel


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _to_dispositions(raw: object) -> tuple[Disposition, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[Disposition] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        at = _parse(str(item.get("at") or ""))
        try:
            verdict = Verdict(str(item.get("verdict")))
        except ValueError:
            continue
        if at is None:
            continue
        out.append(
            Disposition(
                run_id=int(item.get("run_id") or 0),
                verdict=verdict,
                note=str(item.get("note") or ""),
                at=at,
            )
        )
    return tuple(out)


def _from_dispositions(items: tuple[Disposition, ...]) -> list[dict[str, Any]]:
    return [
        {
            "run_id": item.run_id,
            "verdict": item.verdict.value,
            "note": item.note,
            "at": item.at.isoformat(),
        }
        for item in items
    ]


def _to_domain(model: OpenFindingModel) -> OpenFinding:
    finding = OpenFinding(
        finding=model.finding,
        resolution_test=model.resolution_test,
        finding_id=int(model.id),
        evaluation_id=(
            None if model.evaluation_id is None else int(model.evaluation_id)
        ),
        status=FindingStatus(model.status),
        dispositions=_to_dispositions(model.dispositions_json),
        closed_at=_parse(model.closed_at),
        closing_note=model.closing_note or "",
    )
    created = _parse(model.created_at)
    if created is not None:
        finding.created_at = created
    return finding


class OpenFindingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, finding: OpenFinding) -> OpenFinding:
        model = OpenFindingModel(
            evaluation_id=finding.evaluation_id,
            finding=finding.finding.strip(),
            resolution_test=finding.resolution_test.strip(),
            status=finding.status.value,
            dispositions_json=_from_dispositions(finding.dispositions),
        )
        self._session.add(model)
        await self._session.flush()
        return _to_domain(model)

    async def get_by_id(self, finding_id: int) -> OpenFinding | None:
        model = await self._session.get(OpenFindingModel, finding_id)
        return None if model is None else _to_domain(model)

    async def list_open(self, *, limit: int) -> list[OpenFinding]:
        models = (
            await self._session.scalars(
                select(OpenFindingModel)
                .where(OpenFindingModel.status == FindingStatus.OPEN.value)
                .order_by(OpenFindingModel.id.desc())
                .limit(limit)
            )
        ).all()
        return [_to_domain(model) for model in models]

    async def list_all(self) -> list[OpenFinding]:
        models = (
            await self._session.scalars(
                select(OpenFindingModel).order_by(OpenFindingModel.id.desc())
            )
        ).all()
        return [_to_domain(model) for model in models]

    async def save(self, finding: OpenFinding) -> OpenFinding:
        model = await self._session.get(OpenFindingModel, finding.finding_id)
        if model is None:
            raise ValueError(f"unknown finding: {finding.finding_id}")
        model.status = finding.status.value
        model.dispositions_json = _from_dispositions(finding.dispositions)
        model.closing_note = finding.closing_note or None
        model.closed_at = (
            None if finding.closed_at is None else finding.closed_at.isoformat()
        )
        await self._session.flush()
        return _to_domain(model)


__all__ = ["OpenFindingRepository"]
