"""Where the return curve reads the benchmark, the account's start and its fills."""

from __future__ import annotations

from datetime import date

import pytest

from backend.business.performance import AccountStart, DayFill
from backend.infra.db.models import AccountOrderCacheModel, AccountProfileCacheModel
from backend.infra.repositories import AccountFactsRepository, BenchmarkCloseRepository


@pytest.mark.asyncio
async def test_benchmark_closes_are_kept_per_symbol_and_replaced_in_place(
    session,
) -> None:
    repo = BenchmarkCloseRepository(session)

    await repo.save_closes("000300.SH", {date(2026, 8, 27): 4630.0})
    await repo.save_closes(
        "000300.SH", {date(2026, 8, 27): 4630.28, date(2026, 8, 28): 4609.18}
    )
    await repo.save_closes("000001.SH", {date(2026, 8, 27): 3800.0})
    await session.commit()

    assert await repo.closes("000300.SH") == {
        date(2026, 8, 27): 4630.28,
        date(2026, 8, 28): 4609.18,
    }


@pytest.mark.asyncio
async def test_the_account_starts_where_its_profile_says(session) -> None:
    session.add(
        AccountProfileCacheModel(open_date="2026-08-28", initial_capital=1_000_000.0)
    )
    await session.commit()

    assert await AccountFactsRepository(session).account_start() == AccountStart(
        open_date=date(2026, 8, 28), initial_capital=1_000_000.0
    )


@pytest.mark.asyncio
async def test_no_profile_means_no_start(session) -> None:
    assert await AccountFactsRepository(session).account_start() is None


@pytest.mark.asyncio
async def test_a_fill_belongs_to_the_shanghai_day_it_was_placed(session) -> None:
    def order(order_id: str, submitted_at: str, filled: int) -> AccountOrderCacheModel:
        return AccountOrderCacheModel(
            order_id=order_id,
            symbol="600276",
            stock_name="恒瑞医药",
            direction="BUY",
            quantity=400,
            order_price=45.95,
            status="FILLED" if filled else "REJECTED",
            filled_quantity=filled,
            filled_price=45.95 if filled else None,
            submitted_at=submitted_at,
            updated_at=submitted_at,
        )

    # 01:35 UTC is 09:35 in Shanghai on the same date; 17:30 UTC on the 8th
    # is already 01:30 on the 9th there.
    session.add(order("a", "2026-10-09T01:35:05+00:00", 400))
    session.add(order("b", "2026-10-08T17:30:00+00:00", 400))
    session.add(order("c", "2026-10-09T01:35:07+00:00", 0))
    await session.commit()

    assert await AccountFactsRepository(session).day_fills() == [
        DayFill(date(2026, 10, 9), "600276", "恒瑞医药", "BUY", 400, 45.95),
        DayFill(date(2026, 10, 9), "600276", "恒瑞医药", "BUY", 400, 45.95),
    ]
