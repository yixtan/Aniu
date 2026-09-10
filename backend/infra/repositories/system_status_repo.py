"""Persistence adapter for the system-status panel.

Each method projects only the columns the panel folds, so a month of runs is
a few hundred small tuples rather than a few hundred trace payloads.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime

from sqlalchemy import Select, and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.business.system_status import (
    MEMORY_WRITE_TOOL,
    TRADE_TOOL,
    DataCallFact,
    DreamFact,
    MemoryActivityFact,
    MemoryInventory,
    RunFact,
    ToolCallFact,
)
from backend.infra.db.models import (
    MemoryActivityModel,
    MemoryDreamModel,
    MemoryItemModel,
    StockApiCallLogModel,
    StrategyRunModel,
    ToolInvocationModel,
)

_TOOL_COMPLETED = "COMPLETED"
_TOOL_FAILED = "FAILED"
_DATA_CALL_SUCCESS = "success"
_SUMMARY_HTML = "html"


def _as_utc(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _optional_utc(value: str | None) -> datetime | None:
    return None if not value else _as_utc(value)



def _dream_fact(row: MemoryDreamModel) -> DreamFact:
    return DreamFact(
        task_id=row.id,
        target_date=date.fromisoformat(row.target_date),
        status=row.status,
        completed_at=_optional_utc(row.completed_at),
        failure_reason=row.failure_reason,
        total_tokens=int(row.total_tokens or 0),
    )


class SystemStatusRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def runs_since(self, since: datetime) -> list[RunFact]:
        statement = select(
            StrategyRunModel.started_at,
            StrategyRunModel.status,
            StrategyRunModel.summary_render_mode,
            StrategyRunModel.total_tokens,
        ).where(StrategyRunModel.started_at >= since.isoformat())
        rows = (await self._session.execute(statement)).all()
        return [
            RunFact(
                started_at=_as_utc(started_at),
                status=status,
                summary_html=render_mode == _SUMMARY_HTML,
                total_tokens=int(total_tokens or 0),
            )
            for started_at, status, render_mode, total_tokens in rows
        ]

    async def tool_calls_since(self, since: datetime) -> list[ToolCallFact]:
        # A row still STARTED is either in flight or died mid-call; neither is
        # a result, so it is left out rather than guessed at.
        statement = select(
            ToolInvocationModel.created_at,
            ToolInvocationModel.tool_name,
            ToolInvocationModel.status,
        ).where(
            ToolInvocationModel.created_at >= since.isoformat(),
            ToolInvocationModel.tool_name.in_((TRADE_TOOL, MEMORY_WRITE_TOOL)),
            ToolInvocationModel.status.in_((_TOOL_COMPLETED, _TOOL_FAILED)),
        )
        rows = (await self._session.execute(statement)).all()
        return [
            ToolCallFact(
                created_at=_as_utc(created_at),
                tool_name=tool_name,
                succeeded=status == _TOOL_COMPLETED,
            )
            for created_at, tool_name, status in rows
        ]

    async def data_calls_since(self, since: datetime) -> list[DataCallFact]:
        statement = select(
            StockApiCallLogModel.created_at, StockApiCallLogModel.status
        ).where(StockApiCallLogModel.created_at >= since.isoformat())
        rows = (await self._session.execute(statement)).all()
        return [
            DataCallFact(
                created_at=_as_utc(created_at),
                succeeded=status == _DATA_CALL_SUCCESS,
            )
            for created_at, status in rows
        ]

    async def memory_activities_since(
        self, since: datetime
    ) -> list[MemoryActivityFact]:
        statement = select(
            MemoryActivityModel.created_at,
            MemoryActivityModel.operation,
            MemoryActivityModel.task_id,
            MemoryActivityModel.content,
        ).where(MemoryActivityModel.created_at >= since.isoformat())
        return await self._activities(statement)

    async def memory_activities_for_tasks(
        self, task_ids: Sequence[int]
    ) -> list[MemoryActivityFact]:
        if not task_ids:
            return []
        statement = select(
            MemoryActivityModel.created_at,
            MemoryActivityModel.operation,
            MemoryActivityModel.task_id,
            MemoryActivityModel.content,
        ).where(MemoryActivityModel.task_id.in_(list(task_ids)))
        return await self._activities(statement)

    async def _activities(
        self, statement: Select[tuple[str, str, int, str]]
    ) -> list[MemoryActivityFact]:
        rows = (await self._session.execute(statement)).all()
        return [
            MemoryActivityFact(
                created_at=_as_utc(created_at),
                operation=operation,
                task_id=int(task_id),
                query=content or "",
            )
            for created_at, operation, task_id, content in rows
        ]

    async def dreams_since(self, since: datetime) -> list[DreamFact]:
        statement = select(MemoryDreamModel).where(
            MemoryDreamModel.completed_at.is_not(None),
            MemoryDreamModel.completed_at >= since.isoformat(),
        )
        rows = (await self._session.scalars(statement)).all()
        return [_dream_fact(row) for row in rows]

    async def recent_dreams(self, limit: int) -> list[DreamFact]:
        statement = (
            select(MemoryDreamModel)
            .order_by(MemoryDreamModel.target_date.desc())
            .limit(limit)
        )
        rows = (await self._session.scalars(statement)).all()
        return [_dream_fact(row) for row in rows]

    async def memory_inventory(self) -> MemoryInventory:
        is_live = MemoryItemModel.deleted_at.is_(None)
        statement = select(
            func.sum(case((is_live, 1), else_=0)),
            func.sum(case((MemoryItemModel.deleted_at.is_not(None), 1), else_=0)),
            func.sum(
                case(
                    (and_(is_live, MemoryItemModel.replaces_json.is_not(None)), 1),
                    else_=0,
                )
            ),
        )
        live, deleted, with_lineage = (await self._session.execute(statement)).one()
        return MemoryInventory(
            live=int(live or 0),
            deleted=int(deleted or 0),
            with_lineage=int(with_lineage or 0),
        )


__all__ = ["SystemStatusRepository"]
