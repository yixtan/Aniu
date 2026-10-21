"""What the cached account says about where its return curve starts and moves."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.business.performance import AccountStart, DayFill
from backend.infra.db.models import AccountOrderCacheModel, AccountProfileCacheModel

_MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


class AccountFactsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def account_start(self) -> AccountStart | None:
        profile = (
            await self._session.scalars(select(AccountProfileCacheModel).limit(1))
        ).first()
        if (
            profile is None
            or not profile.open_date
            or profile.initial_capital is None
            or profile.initial_capital <= 0
        ):
            return None
        return AccountStart(
            open_date=date.fromisoformat(profile.open_date[:10]),
            initial_capital=float(profile.initial_capital),
        )

    async def day_fills(self) -> list[DayFill]:
        # The broker's order list is the whole history, and a day order fills
        # on the day it was placed or not at all, so the day it was placed is
        # the day it filled.
        orders = (
            await self._session.scalars(
                select(AccountOrderCacheModel)
                .where(AccountOrderCacheModel.filled_quantity > 0)
                .order_by(AccountOrderCacheModel.submitted_at.asc())
            )
        ).all()
        fills: list[DayFill] = []
        for order in orders:
            placed = order.submitted_at or order.updated_at
            if not placed or order.filled_price is None:
                continue
            fills.append(
                DayFill(
                    trade_date=datetime.fromisoformat(placed)
                    .astimezone(_MARKET_TIMEZONE)
                    .date(),
                    symbol=order.symbol,
                    stock_name=order.stock_name,
                    direction=order.direction,
                    quantity=int(order.filled_quantity),
                    price=float(order.filled_price),
                )
            )
        return fills
