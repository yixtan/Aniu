"""Assemble the fill record one run at a time."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime

from backend.business.fill_record.models import (
    AttributedOrder,
    DayTotals,
    FillRecord,
)
from backend.business.fill_record.ports import FillRecordRepositoryPort


class FillRecordService:
    def __init__(self, repository: FillRecordRepositoryPort) -> None:
        self._repository = repository

    async def for_run(self, run_id: int) -> FillRecord:
        return assemble_fill_record(run_id, await self._repository.attributed_orders())


def assemble_fill_record(
    run_id: int,
    orders: list[AttributedOrder],
) -> FillRecord:
    ordered: defaultdict[date, int] = defaultdict(int)
    filled: defaultdict[date, int] = defaultdict(int)
    runs: defaultdict[date, set[int]] = defaultdict(set)
    for order in orders:
        if order.submitted_at is None:
            # No day to file it under. It still counts as unattributed below,
            # so it stays visible rather than vanishing from every total.
            continue
        day = order.submitted_at.date()
        ordered[day] += 1
        filled[day] += order.filled
        if order.run_id is not None:
            runs[day].add(order.run_id)
    return FillRecord(
        run_id=run_id,
        own_orders=_own_orders(run_id, orders),
        days=tuple(
            DayTotals(
                day=day,
                placing_runs=len(runs[day]),
                ordered=ordered[day],
                filled=filled[day],
            )
            for day in sorted(ordered)
        ),
        unattributed=sum(order.run_id is None for order in orders),
    )


def _own_orders(
    run_id: int,
    orders: list[AttributedOrder],
) -> tuple[AttributedOrder, ...]:
    mine = [order for order in orders if order.run_id == run_id]
    dated = [order for order in mine if order.submitted_at is not None]
    dated.sort(key=lambda order: order.submitted_at or _EPOCH)
    return tuple(dated) + tuple(order for order in mine if order.submitted_at is None)


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


__all__ = ["FillRecordService", "assemble_fill_record"]
