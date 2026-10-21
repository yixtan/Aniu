"""History failures roll back separately, survive restart and clear on recovery."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.business.account.service import AccountAppService
from backend.business.performance import DailyEquity
from backend.infra.integrations.daily_equity_recorder import DailyEquityRecorder
from backend.infra.repositories.account_cache_repo import AccountCacheRepository
from backend.infra.repositories.daily_equity_repo import DailyEquityRepository
from backend.tests.business.test_account_app_service import FakePortfolioClient


def equity() -> DailyEquity:
    return DailyEquity(
        trade_date=date(2026, 10, 21),
        total_asset=100_000,
        available_cash=50_000,
        frozen_cash=0,
        market_value=50_000,
        captured_at=datetime(2026, 10, 21, 7, 10, tzinfo=UTC),
    )


async def fail_writes(session_factory, table: str) -> None:
    # A real failed SQL flush is stronger than a fake repository raising:
    # the session is unusable until it is rolled back.
    assert table in {"account_daily_equity", "equity_recording_status"}
    async with session_factory() as session:
        await session.execute(
            text(
                f"CREATE TRIGGER fail_{table} BEFORE INSERT ON {table} "
                "BEGIN SELECT RAISE(FAIL, 'private SQL detail "
                "api_key=test-secret'); END"
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_success_is_committed_with_its_status_and_survives_restart(
    session_factory,
) -> None:
    await DailyEquityRecorder(session_factory).record_day(equity())

    status = await DailyEquityRecorder(session_factory).recording_status()
    assert status is not None
    assert status.trade_date == equity().trade_date
    assert status.error_message is None
    assert status.last_success_at == status.attempted_at
    async with session_factory() as session:
        assert await DailyEquityRepository(session).list_days() == [equity()]


@pytest.mark.asyncio
async def test_failed_history_flush_preserves_account_cache_and_persists_warning(
    session_factory,
) -> None:
    recorder = DailyEquityRecorder(session_factory)
    await recorder.record_day(equity())
    previous = await recorder.recording_status()
    await fail_writes(session_factory, "account_daily_equity")

    async with session_factory() as session:
        service = AccountAppService(
            portfolio_client=FakePortfolioClient(),
            account_cache_repo=AccountCacheRepository(session),
            committer=session,
            equity_recorder=recorder,
            clock=lambda: datetime(2026, 10, 22, 2, 0, tzinfo=UTC),
        )
        assert (await service.refresh_account_cache()).status == "refreshed"
        # The cache session is still usable after a real failed history flush.
        dashboard = await service.get_account_dashboard()
        assert dashboard.overview.total_asset == 100_000
    warning = await DailyEquityRecorder(session_factory).recording_status()
    assert warning is not None and previous is not None
    assert warning.trade_date == date(2026, 10, 22)
    assert warning.last_success_at == previous.last_success_at
    assert warning.error_message is not None
    assert "test-secret" not in warning.error_message
    assert "INSERT" not in warning.error_message
    async with session_factory() as session:
        assert await DailyEquityRepository(session).list_days() == [equity()]

    async with session_factory() as session:
        await session.execute(text("DROP TRIGGER fail_account_daily_equity"))
        await session.commit()
    await recorder.record_day(equity())
    recovered = await DailyEquityRecorder(session_factory).recording_status()
    assert recovered is not None
    assert recovered.error_message is None
    assert recovered.last_success_at == recovered.attempted_at
    assert recovered.attempted_at > warning.attempted_at


@pytest.mark.asyncio
async def test_a_warning_remains_visible_if_its_own_db_write_also_fails(
    session_factory,
) -> None:
    recorder = DailyEquityRecorder(session_factory)
    await fail_writes(session_factory, "account_daily_equity")
    await fail_writes(session_factory, "equity_recording_status")

    with pytest.raises(IntegrityError):
        await recorder.record_day(equity())

    status = await recorder.recording_status()
    assert status is not None
    assert status.error_message is not None
    assert "test-secret" not in status.error_message


@pytest.mark.asyncio
async def test_a_failed_status_commit_cannot_claim_that_equity_was_saved(
    session_factory,
) -> None:
    recorder = DailyEquityRecorder(session_factory)
    await fail_writes(session_factory, "equity_recording_status")

    with pytest.raises(IntegrityError):
        await recorder.record_day(equity())

    async with session_factory() as session:
        assert await DailyEquityRepository(session).list_days() == []
    status = await recorder.recording_status()
    assert status is not None and status.error_message is not None
    assert status.last_success_at is None
