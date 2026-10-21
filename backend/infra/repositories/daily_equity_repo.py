"""Persistence for the account's value at each trading day's close."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.business.performance import DailyEquity, EquitySource
from backend.infra.db.models import AccountDailyEquityModel


def _to_domain(model: AccountDailyEquityModel) -> DailyEquity:
    return DailyEquity(
        trade_date=date.fromisoformat(model.trade_date),
        total_asset=float(model.total_asset),
        available_cash=float(model.available_cash),
        frozen_cash=float(model.frozen_cash),
        market_value=float(model.market_value),
        captured_at=datetime.fromisoformat(model.captured_at),
        source=EquitySource(model.source),
    )


class DailyEquityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_day(self, equity: DailyEquity) -> None:
        # One row per day, replaced in place: each refresh is a later reading
        # of the same day, and the last one after the close is the close.
        model = await self._session.get(
            AccountDailyEquityModel, equity.trade_date.isoformat()
        )
        if model is None:
            model = AccountDailyEquityModel(trade_date=equity.trade_date.isoformat())
            self._session.add(model)
        model.total_asset = equity.total_asset
        model.available_cash = equity.available_cash
        model.frozen_cash = equity.frozen_cash
        model.market_value = equity.market_value
        model.captured_at = equity.captured_at.isoformat()
        model.source = equity.source.value
        await self._session.flush()

    async def list_days(self) -> list[DailyEquity]:
        models = (
            await self._session.scalars(
                select(AccountDailyEquityModel).order_by(
                    AccountDailyEquityModel.trade_date.asc()
                )
            )
        ).all()
        return [_to_domain(model) for model in models]
