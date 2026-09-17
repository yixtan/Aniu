"""Join placed orders back to the run that placed them.

The owner is read out of the trade tool's own reply, which carries the broker's
`orderID`, and matched to the order cache by that id.

It is deliberately NOT a column on `account_orders_cache`: that table is
deleted and rebuilt on every account refresh (see `AccountCacheRepository`), so
an owner stored there would be wiped twice an hour. Nothing about who placed an
order belongs in a table that is rebuilt from the broker's answer.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.business.fill_record.models import AttributedOrder
from backend.infra.db.models import AccountOrderCacheModel, ToolInvocationModel

TRADE_TOOL = "trade"
COMPLETED = "COMPLETED"


def _order_id_of(result_json: str | None) -> str | None:
    """The broker's order id out of one trade reply, or None.

    A reply that is missing, malformed or shaped differently yields None and
    the order stays unattributed. Guessing an owner from timing instead would
    put one run's orders under another's name, which is the single failure
    this record exists to prevent.
    """

    if not result_json:
        return None
    try:
        payload: Any = json.loads(result_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    order_id = data.get("orderID")
    return str(order_id) if order_id not in (None, "") else None


class FillRecordRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def attributed_orders(self) -> list[AttributedOrder]:
        owner = await self._owners()
        rows = (
            await self._session.execute(
                select(
                    AccountOrderCacheModel.order_id,
                    AccountOrderCacheModel.symbol,
                    AccountOrderCacheModel.stock_name,
                    AccountOrderCacheModel.direction,
                    AccountOrderCacheModel.quantity,
                    AccountOrderCacheModel.order_price,
                    AccountOrderCacheModel.status,
                    AccountOrderCacheModel.submitted_at,
                )
            )
        ).all()
        return [
            AttributedOrder(
                order_id=str(row[0]),
                run_id=owner.get(str(row[0])),
                symbol=row[1],
                stock_name=row[2],
                direction=row[3],
                quantity=int(row[4] or 0),
                order_price=None if row[5] is None else float(row[5]),
                status=row[6],
                submitted_at=_parse(row[7]),
            )
            for row in rows
        ]

    async def _owners(self) -> dict[str, int]:
        rows = (
            await self._session.execute(
                select(
                    ToolInvocationModel.run_id,
                    ToolInvocationModel.result_json,
                ).where(
                    ToolInvocationModel.tool_name == TRADE_TOOL,
                    ToolInvocationModel.status == COMPLETED,
                )
            )
        ).all()
        owner: dict[str, int] = {}
        for run_id, result_json in rows:
            order_id = _order_id_of(result_json)
            if order_id is not None:
                owner[order_id] = int(run_id)
        return owner


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


__all__ = ["FillRecordRepository"]
