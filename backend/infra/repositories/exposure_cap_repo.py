"""Persistence for the cap a run declared for itself."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.business.exposure.models import MAX_REASON_LENGTH, ExposureCap
from backend.infra.db.models import ExposureCapModel


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _to_domain(model: ExposureCapModel) -> ExposureCap:
    declared = _parse(model.declared_at)
    fields: dict[str, object] = {
        "run_id": int(model.run_id),
        "cap_pct": float(model.cap_pct),
        "basis": model.basis,
        "changed_from": model.changed_from,
        "forgone": model.forgone,
    }
    if declared is not None:
        fields["declared_at"] = declared
    return ExposureCap(**fields)  # type: ignore[arg-type]


class ExposureCapRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def declare(self, cap: ExposureCap) -> ExposureCap:
        # One row per run, replaced rather than appended: a run that declares
        # twice changed its mind, and two rows would read as two decisions.
        model = (
            await self._session.scalars(
                select(ExposureCapModel).where(ExposureCapModel.run_id == cap.run_id)
            )
        ).first()
        if model is None:
            model = ExposureCapModel(run_id=cap.run_id)
            self._session.add(model)
        model.cap_pct = cap.cap_pct
        model.basis = cap.basis.strip()[:MAX_REASON_LENGTH]
        model.changed_from = cap.changed_from.strip()[:MAX_REASON_LENGTH]
        model.forgone = cap.forgone.strip()[:MAX_REASON_LENGTH]
        model.declared_at = cap.declared_at.isoformat()
        await self._session.flush()
        return _to_domain(model)

    async def for_run(self, run_id: int) -> ExposureCap | None:
        model = (
            await self._session.scalars(
                select(ExposureCapModel).where(ExposureCapModel.run_id == run_id)
            )
        ).first()
        return None if model is None else _to_domain(model)

    async def recent(self, *, limit: int) -> list[ExposureCap]:
        models = (
            await self._session.scalars(
                select(ExposureCapModel)
                .order_by(
                    ExposureCapModel.declared_at.desc(),
                    ExposureCapModel.id.desc(),
                )
                .limit(limit)
            )
        ).all()
        return [_to_domain(model) for model in models]


__all__ = ["ExposureCapRepository"]
