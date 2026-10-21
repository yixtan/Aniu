"""Last automatic equity-write result, retained across app restarts."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.business.performance.models import EquityRecordingStatus
from backend.infra.db.models import EquityRecordingStatusModel


class EquityRecordingStatusRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def recording_status(self) -> EquityRecordingStatus | None:
        model = await self._session.get(EquityRecordingStatusModel, 1)
        if model is None:
            return None
        return EquityRecordingStatus(
            trade_date=date.fromisoformat(model.trade_date),
            attempted_at=datetime.fromisoformat(model.attempted_at),
            last_success_at=(
                datetime.fromisoformat(model.last_success_at)
                if model.last_success_at
                else None
            ),
            error_message=model.error_message,
        )

    async def save(self, status: EquityRecordingStatus) -> None:
        values = {
            "trade_date": status.trade_date.isoformat(),
            "attempted_at": status.attempted_at.isoformat(),
            "last_success_at": (
                status.last_success_at.isoformat() if status.last_success_at else None
            ),
            "error_message": status.error_message,
        }
        statement = insert(EquityRecordingStatusModel).values(id=1, **values)
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[EquityRecordingStatusModel.id],
                set_=values,
                where=(
                    EquityRecordingStatusModel.attempted_at
                    <= status.attempted_at.isoformat()
                ),
            )
        )
