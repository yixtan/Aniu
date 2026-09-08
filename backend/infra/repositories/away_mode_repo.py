"""Persistence for the away-mode switch."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.business.away import AwayMode
from backend.infra.db.models import AwayModeModel

_ROW_ID = 1


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(tz=UTC)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        # Unreadable stamp means "not switched on for any day we can name",
        # which is the safe reading: away mode stays off.
        return None


class AwayModeRepository:
    """The single away-mode row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> AwayMode | None:
        model = await self._session.get(AwayModeModel, _ROW_ID)
        return None if model is None else self._to_entity(model)

    async def save(self, mode: AwayMode) -> AwayMode:
        model = await self._session.get(AwayModeModel, _ROW_ID)
        if model is None:
            model = AwayModeModel(id=_ROW_ID)
            self._session.add(model)
        model.active_date = mode.active_date.isoformat() if mode.active_date else None
        await self._session.flush()
        return self._to_entity(model)

    def _to_entity(self, model: AwayModeModel) -> AwayMode:
        return AwayMode(
            active_date=_parse_date(model.active_date),
            updated_at=_parse_datetime(model.updated_at),
        )


__all__ = ["AwayModeRepository"]
