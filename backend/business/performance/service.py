"""The account's value day by day, beside the benchmark over the same days."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.business.performance.dto import (
    ReturnCurveDayDTO,
    ReturnCurveDTO,
    ReturnCurveFillDTO,
)
from backend.business.performance.models import (
    BENCHMARK_NAME,
    BENCHMARK_SYMBOL,
    DailyEquity,
    EquitySource,
)
from backend.business.performance.ports import (
    AccountFactsPort,
    BenchmarkCloseRepositoryPort,
    BenchmarkHistoryPort,
    DailyEquityRepositoryPort,
    TradingCalendarPort,
)
from backend.business.shared import CommitterPort

logger = logging.getLogger(__name__)
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
# A recorded day keeps being overwritten through the afternoon; one last read
# after the bell is the close. The index's own close is settled a little
# later, so a day's benchmark is only fetched once it cannot still move.
SESSION_CLOSES = time(15, 0)
BENCHMARK_SETTLED = time(15, 30)


class PerformanceService:
    def __init__(
        self,
        *,
        equity_repo: DailyEquityRepositoryPort,
        benchmark_repo: BenchmarkCloseRepositoryPort,
        benchmark_history: BenchmarkHistoryPort,
        account_facts: AccountFactsPort,
        trading_calendar: TradingCalendarPort,
        committer: CommitterPort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._equity_repo = equity_repo
        self._benchmark_repo = benchmark_repo
        self._benchmark_history = benchmark_history
        self._account_facts = account_facts
        self._calendar = trading_calendar
        self._committer = committer
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    async def return_curve(self) -> ReturnCurveDTO:
        now = self._clock().astimezone(MARKET_TIMEZONE)
        start = await self._account_facts.account_start()
        days = await self._equity_repo.list_days()
        base_date = (
            None
            if start is None
            else self._calendar.last_trading_day_on_or_before(
                start.open_date - timedelta(days=1)
            )
        )
        settled = [
            day.trade_date
            for day in days
            if self._benchmark_settled(day.trade_date, now)
        ]
        closes = await self._benchmark_closes(
            ([base_date] if base_date is not None else []) + settled
        )
        fills: dict[date, list[ReturnCurveFillDTO]] = defaultdict(list)
        for fill in await self._account_facts.day_fills():
            fills[fill.trade_date].append(
                ReturnCurveFillDTO(
                    symbol=fill.symbol,
                    stock_name=fill.stock_name,
                    direction=fill.direction,
                    quantity=fill.quantity,
                    price=fill.price,
                )
            )
        return ReturnCurveDTO(
            benchmark_name=BENCHMARK_NAME,
            initial_capital=None if start is None else start.initial_capital,
            open_date=None if start is None else start.open_date,
            base_date=base_date,
            base_benchmark_close=None if base_date is None else closes.get(base_date),
            days=[
                ReturnCurveDayDTO(
                    date=day.trade_date,
                    total_asset=day.total_asset,
                    market_value=day.market_value,
                    source=day.source.value,
                    is_close=self._is_close(day),
                    benchmark_close=closes.get(day.trade_date),
                    fills=fills.get(day.trade_date, []),
                )
                for day in days
            ],
        )

    async def _benchmark_closes(self, wanted: list[date]) -> dict[date, float]:
        """Stored closes, topped up from upstream only for days not yet kept.

        The dashboard polls; asking upstream on every poll would re-download
        the same history all day. A settled close never changes, so it is
        asked for once: only a day after the newest kept close triggers a
        request. A gap behind it is one upstream did not have last time, and
        asking on every poll would not change that.

        The request that does go out always starts from the earliest day
        wanted, so it also fills any older day that has since gained an
        account value — days reconstructed after the fact, say.
        """

        closes = await self._benchmark_repo.closes(BENCHMARK_SYMBOL)
        newest = max(closes, default=None)
        missing = [
            day
            for day in wanted
            if day not in closes and (newest is None or day > newest)
        ]
        if not missing:
            return closes
        start, end = min(wanted), max(missing)
        try:
            fetched = await self._benchmark_history.daily_closes(
                BENCHMARK_SYMBOL, start=start, end=end
            )
        except Exception:
            # The account's own line does not depend on it; draw that alone.
            logger.warning("benchmark closes were not fetched", exc_info=True)
            return closes
        kept = {day: close for day, close in fetched.items() if start <= day <= end}
        if kept:
            await self._benchmark_repo.save_closes(BENCHMARK_SYMBOL, kept)
            if self._committer is not None:
                await self._committer.commit()
        return {**closes, **kept}

    @staticmethod
    def _benchmark_settled(day: date, now: datetime) -> bool:
        return day < now.date() or (
            day == now.date() and now.time() >= BENCHMARK_SETTLED
        )

    @staticmethod
    def _is_close(day: DailyEquity) -> bool:
        if day.source is EquitySource.RECONSTRUCTED:
            return True
        captured = day.captured_at.astimezone(MARKET_TIMEZONE)
        return captured.date() > day.trade_date or captured.time() >= SESSION_CLOSES


__all__ = ["PerformanceService"]
