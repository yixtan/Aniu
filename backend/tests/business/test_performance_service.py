"""The return curve: the account's closes beside the benchmark's."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta

import pytest

from backend.business.performance import (
    AccountStart,
    DailyEquity,
    DayFill,
    EquitySource,
    PerformanceService,
)

OPEN = date(2026, 8, 28)  # a Friday
BASE = date(2026, 8, 27)


def _shanghai(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC) - (
        timedelta(hours=8)
    )


def _day(
    day: date,
    total_asset: float,
    *,
    hour: int = 15,
    minute: int = 10,
    source: EquitySource = EquitySource.RECORDED,
) -> DailyEquity:
    return DailyEquity(
        trade_date=day,
        total_asset=total_asset,
        available_cash=total_asset - 50_000.0,
        frozen_cash=0.0,
        market_value=50_000.0,
        captured_at=_shanghai(day, hour, minute),
        source=source,
    )


class EquityRepo:
    def __init__(self, days: list[DailyEquity]) -> None:
        self.days = days

    async def record_day(self, equity: DailyEquity) -> None:
        self.days.append(equity)

    async def list_days(self) -> list[DailyEquity]:
        return sorted(self.days, key=lambda day: day.trade_date)


class BenchmarkRepo:
    def __init__(self, closes: dict[date, float] | None = None) -> None:
        self.stored = dict(closes or {})

    async def closes(self, symbol: str) -> dict[date, float]:
        assert symbol == "000300.SH"
        return dict(self.stored)

    async def save_closes(self, symbol: str, closes: Mapping[date, float]) -> None:
        assert symbol == "000300.SH"
        self.stored.update(closes)


class History:
    def __init__(self, closes: dict[date, float], *, fails: bool = False) -> None:
        self.available = closes
        self.fails = fails
        self.requests: list[tuple[date, date]] = []

    async def daily_closes(
        self, symbol: str, *, start: date, end: date
    ) -> dict[date, float]:
        assert symbol == "000300.SH"
        self.requests.append((start, end))
        if self.fails:
            raise RuntimeError("upstream down")
        return {
            day: close for day, close in self.available.items() if start <= day <= end
        }


class Facts:
    def __init__(
        self,
        start: AccountStart | None = AccountStart(OPEN, 1_000_000.0),
        fills: list[DayFill] | None = None,
    ) -> None:
        self.start = start
        self.fills = fills or []

    async def account_start(self) -> AccountStart | None:
        return self.start

    async def day_fills(self) -> list[DayFill]:
        return list(self.fills)


class Calendar:
    def last_trading_day_on_or_before(self, day: date) -> date:
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return day


class Committer:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


INDEX = {
    BASE: 4630.28,
    OPEN: 4609.18,
    date(2026, 8, 31): 4620.00,
    date(2026, 9, 1): 4600.00,
    date(2026, 9, 2): 4580.00,
}


def _service(
    *,
    days: list[DailyEquity],
    now: datetime,
    benchmark: BenchmarkRepo | None = None,
    history: History | None = None,
    facts: Facts | None = None,
    committer: Committer | None = None,
) -> PerformanceService:
    return PerformanceService(
        equity_repo=EquityRepo(days),
        benchmark_repo=benchmark or BenchmarkRepo(),
        benchmark_history=history or History(INDEX),
        account_facts=facts or Facts(),
        trading_calendar=Calendar(),
        committer=committer,
        clock=lambda: now,
    )


@pytest.mark.asyncio
async def test_both_lines_start_from_the_day_before_the_account_opened() -> None:
    days = [_day(OPEN, 1_000_000.0), _day(date(2026, 8, 31), 1_008_036.0)]

    curve = await _service(
        days=days, now=_shanghai(date(2026, 9, 1), 10)
    ).return_curve()

    assert curve.benchmark_name == "沪深300"
    assert curve.initial_capital == 1_000_000.0
    assert curve.open_date == OPEN
    assert curve.base_date == BASE
    assert curve.base_benchmark_close == 4630.28
    assert [(day.date, day.benchmark_close) for day in curve.days] == [
        (OPEN, 4609.18),
        (date(2026, 8, 31), 4620.00),
    ]


@pytest.mark.asyncio
async def test_fills_are_listed_under_their_day() -> None:
    fills = [
        DayFill(date(2026, 8, 31), "600276", "恒瑞医药", "BUY", 400, 45.95),
        DayFill(date(2026, 8, 31), "600150", "中国船舶", "BUY", 600, 39.0),
    ]

    curve = await _service(
        days=[_day(OPEN, 1e6), _day(date(2026, 8, 31), 1e6)],
        now=_shanghai(date(2026, 9, 1), 10),
        facts=Facts(fills=fills),
    ).return_curve()

    assert curve.days[0].fills == []
    assert [(fill.symbol, fill.quantity) for fill in curve.days[1].fills] == [
        ("600276", 400),
        ("600150", 600),
    ]


@pytest.mark.asyncio
async def test_a_settled_close_is_fetched_once_and_then_kept() -> None:
    benchmark = BenchmarkRepo()
    history = History(INDEX)
    committer = Committer()
    days = [_day(OPEN, 1e6), _day(date(2026, 8, 31), 1e6)]
    now = _shanghai(date(2026, 9, 1), 10)

    for _ in range(3):
        await _service(
            days=days,
            now=now,
            benchmark=benchmark,
            history=history,
            committer=committer,
        ).return_curve()

    assert history.requests == [(BASE, date(2026, 8, 31))]
    assert committer.commits == 1
    assert benchmark.stored == {
        BASE: 4630.28,
        OPEN: 4609.18,
        date(2026, 8, 31): 4620.00,
    }


@pytest.mark.asyncio
async def test_todays_index_close_waits_until_it_has_settled() -> None:
    today = date(2026, 9, 1)
    days = [_day(OPEN, 1e6), _day(today, 1e6, hour=14)]
    benchmark = BenchmarkRepo({BASE: 4630.28, OPEN: 4609.18})
    history = History(INDEX)

    during = await _service(
        days=days,
        now=_shanghai(today, 14, 30),
        benchmark=benchmark,
        history=history,
    ).return_curve()
    after = await _service(
        days=days,
        now=_shanghai(today, 15, 35),
        benchmark=benchmark,
        history=history,
    ).return_curve()

    assert during.days[-1].benchmark_close is None
    assert after.days[-1].benchmark_close == 4600.00
    assert history.requests == [(BASE, today)]


@pytest.mark.asyncio
async def test_a_new_day_also_fills_older_days_that_gained_a_value() -> None:
    # 8/31 was reconstructed after its neighbours' closes were already kept.
    benchmark = BenchmarkRepo({BASE: 4630.28, OPEN: 4609.18, date(2026, 9, 1): 4600.0})
    days = [
        _day(OPEN, 1e6),
        _day(date(2026, 8, 31), 1e6, source=EquitySource.RECONSTRUCTED),
        _day(date(2026, 9, 1), 1e6),
        _day(date(2026, 9, 2), 1e6),
    ]
    history = History(INDEX)

    curve = await _service(
        days=days,
        now=_shanghai(date(2026, 9, 3), 10),
        benchmark=benchmark,
        history=history,
    ).return_curve()

    assert history.requests == [(BASE, date(2026, 9, 2))]
    assert [day.benchmark_close for day in curve.days] == [
        4609.18,
        4620.0,
        4600.0,
        4580.0,
    ]


@pytest.mark.asyncio
async def test_a_gap_behind_the_newest_close_is_not_asked_for_on_every_poll() -> None:
    benchmark = BenchmarkRepo({BASE: 4630.28, date(2026, 8, 31): 4620.0})
    history = History(INDEX)

    curve = await _service(
        days=[_day(OPEN, 1e6), _day(date(2026, 8, 31), 1e6)],
        now=_shanghai(date(2026, 9, 1), 10),
        benchmark=benchmark,
        history=history,
    ).return_curve()

    assert history.requests == []
    assert curve.days[0].benchmark_close is None


@pytest.mark.asyncio
async def test_without_the_benchmark_the_account_line_still_draws(caplog) -> None:
    curve = await _service(
        days=[_day(OPEN, 1e6)],
        now=_shanghai(date(2026, 9, 1), 10),
        history=History(INDEX, fails=True),
    ).return_curve()

    assert [day.total_asset for day in curve.days] == [1e6]
    assert curve.days[0].benchmark_close is None
    assert curve.base_benchmark_close is None
    assert "benchmark closes were not fetched" in caplog.text


@pytest.mark.asyncio
async def test_a_day_read_before_the_bell_is_not_called_a_close() -> None:
    days = [
        _day(OPEN, 1e6, hour=14, minute=40),
        _day(date(2026, 8, 31), 1e6, hour=15, minute=0),
        _day(
            date(2026, 9, 1), 1e6, hour=9, minute=50, source=EquitySource.RECONSTRUCTED
        ),
    ]

    curve = await _service(
        days=days, now=_shanghai(date(2026, 9, 2), 10)
    ).return_curve()

    assert [day.is_close for day in curve.days] == [False, True, True]


@pytest.mark.asyncio
async def test_an_account_that_has_not_reported_its_start_has_no_base() -> None:
    curve = await _service(
        days=[], now=_shanghai(date(2026, 9, 1), 10), facts=Facts(start=None)
    ).return_curve()

    assert curve.initial_capital is None
    assert curve.base_date is None
    assert curve.days == []
