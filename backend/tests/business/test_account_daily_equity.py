"""An account refresh keeps the day's value for the return curve."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from backend.business.account.service import AccountAppService
from backend.business.performance import DailyEquity, EquitySource
from backend.tests.business.test_account_app_service import (
    FakeAccountCacheRepo,
    FakePortfolioClient,
)


class RecordingEquityRepo:
    def __init__(self) -> None:
        self.days: dict[date, DailyEquity] = {}

    async def record_day(self, equity: DailyEquity) -> None:
        self.days[equity.trade_date] = equity


class BrokenEquityRepo:
    async def record_day(self, equity: DailyEquity) -> None:
        del equity
        raise RuntimeError("disk is full")


class Calendar:
    """2026-10-17 and 18 are a weekend; every other day trades."""

    def last_trading_day_on_or_before(self, day: date) -> date:
        if day == date(2026, 10, 18):
            return date(2026, 10, 16)
        if day == date(2026, 10, 17):
            return date(2026, 10, 16)
        return day


class Committer:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _service(
    *,
    at: datetime,
    equity_recorder: object,
    committer: Committer | None = None,
) -> AccountAppService:
    return AccountAppService(
        portfolio_client=FakePortfolioClient(),
        account_cache_repo=FakeAccountCacheRepo(),
        committer=committer,
        trading_calendar=Calendar(),
        equity_recorder=equity_recorder,  # type: ignore[arg-type]
        clock=lambda: at,
    )


@pytest.mark.asyncio
async def test_a_refresh_during_the_session_records_the_days_value() -> None:
    repo = RecordingEquityRepo()
    committer = Committer()
    # 10:00 in Shanghai.
    at = datetime(2026, 10, 21, 2, 0, tzinfo=UTC)

    await _service(
        at=at, equity_recorder=repo, committer=committer
    ).refresh_account_cache()

    assert repo.days == {
        date(2026, 10, 21): DailyEquity(
            trade_date=date(2026, 10, 21),
            total_asset=100000.0,
            available_cash=20000.0,
            frozen_cash=1000.0,
            market_value=79000.0,
            captured_at=at,
            source=EquitySource.RECORDED,
        )
    }
    # The cache commits first, the day's value after it.
    assert committer.commits == 2


@pytest.mark.asyncio
async def test_the_day_is_the_shanghai_date_not_the_utc_one() -> None:
    repo = RecordingEquityRepo()
    # 2026-10-21 23:30 UTC is already 07:30 on the 22nd in Shanghai — before
    # the open, so nothing; 01:40 UTC on the 22nd is 09:40 there.
    await _service(
        at=datetime(2026, 10, 21, 23, 30, tzinfo=UTC), equity_recorder=repo
    ).refresh_account_cache()
    await _service(
        at=datetime(2026, 10, 22, 1, 40, tzinfo=UTC), equity_recorder=repo
    ).refresh_account_cache()

    assert list(repo.days) == [date(2026, 10, 22)]


@pytest.mark.asyncio
async def test_before_the_open_yesterdays_close_is_not_filed_under_today() -> None:
    repo = RecordingEquityRepo()

    # 09:03 in Shanghai: the automatic refresh runs, the session has not.
    await _service(
        at=datetime(2026, 10, 21, 1, 3, tzinfo=UTC), equity_recorder=repo
    ).refresh_account_cache()

    assert repo.days == {}


@pytest.mark.asyncio
async def test_a_refresh_on_a_non_trading_day_records_nothing() -> None:
    repo = RecordingEquityRepo()

    # Saturday 14:00 in Shanghai, refreshed by hand.
    await _service(
        at=datetime(2026, 10, 17, 6, 0, tzinfo=UTC), equity_recorder=repo
    ).refresh_account_cache()

    assert repo.days == {}


@pytest.mark.asyncio
async def test_a_failure_to_record_does_not_fail_the_refresh(caplog) -> None:
    result = await _service(
        at=datetime(2026, 10, 21, 2, 0, tzinfo=UTC),
        equity_recorder=BrokenEquityRepo(),
    ).refresh_account_cache()

    assert result.status == "refreshed"
    assert "daily equity was not recorded" in caplog.text
