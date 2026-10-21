"""Persistence for an index's settled daily closes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infra.db.models import BenchmarkDailyCloseModel, utc_now_iso


class BenchmarkCloseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def closes(self, symbol: str) -> dict[date, float]:
        models = (
            await self._session.scalars(
                select(BenchmarkDailyCloseModel).where(
                    BenchmarkDailyCloseModel.symbol == symbol
                )
            )
        ).all()
        return {date.fromisoformat(model.trade_date): model.close for model in models}

    async def save_closes(self, symbol: str, closes: Mapping[date, float]) -> None:
        fetched_at = utc_now_iso()
        for day, close in closes.items():
            model = await self._session.get(
                BenchmarkDailyCloseModel, (symbol, day.isoformat())
            )
            if model is None:
                model = BenchmarkDailyCloseModel(
                    symbol=symbol, trade_date=day.isoformat()
                )
                self._session.add(model)
            model.close = float(close)
            model.fetched_at = fetched_at
        await self._session.flush()
