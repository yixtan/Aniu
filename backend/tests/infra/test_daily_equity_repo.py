"""The daily equity table keeps one row per trading day."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from backend.business.performance import DailyEquity, EquitySource
from backend.infra.repositories import DailyEquityRepository


def _equity(day: date, total_asset: float, hour: int) -> DailyEquity:
    return DailyEquity(
        trade_date=day,
        total_asset=total_asset,
        available_cash=total_asset - 50_000.0,
        frozen_cash=0.0,
        market_value=50_000.0,
        captured_at=datetime(day.year, day.month, day.day, hour, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_a_later_reading_of_the_same_day_replaces_the_earlier(session) -> None:
    repo = DailyEquityRepository(session)

    await repo.record_day(_equity(date(2026, 10, 21), 968_000.0, 2))
    await repo.record_day(_equity(date(2026, 10, 21), 969_500.0, 7))
    await session.commit()

    (day,) = await repo.list_days()
    assert day.total_asset == 969_500.0
    assert day.captured_at == datetime(2026, 10, 21, 7, tzinfo=UTC)
    assert day.source is EquitySource.RECORDED


@pytest.mark.asyncio
async def test_days_come_back_oldest_first(session) -> None:
    repo = DailyEquityRepository(session)

    for day in (date(2026, 10, 21), date(2026, 9, 30), date(2026, 10, 9)):
        await repo.record_day(_equity(day, 970_000.0, 7))
    await session.commit()

    assert [day.trade_date for day in await repo.list_days()] == [
        date(2026, 9, 30),
        date(2026, 10, 9),
        date(2026, 10, 21),
    ]
