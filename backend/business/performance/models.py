"""One trading day's closing account value, the point a return curve is drawn from."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class EquitySource(StrEnum):
    # Read from the broker by an account refresh on that day.
    RECORDED = "recorded"
    # Worked out afterwards from the fills and each holding's closing price,
    # for days nobody recorded: before recording existed, or with Aniu down.
    RECONSTRUCTED = "reconstructed"


@dataclass(frozen=True, slots=True)
class DailyEquity:
    trade_date: date
    total_asset: float
    available_cash: float
    frozen_cash: float
    market_value: float
    # When the numbers were read. A recorded day keeps being overwritten until
    # the last refresh after the close, so a time before 15:00 means Aniu
    # stopped early and the row is that moment, not the close.
    captured_at: datetime
    source: EquitySource = EquitySource.RECORDED


# The benchmark the curve is drawn against.
BENCHMARK_SYMBOL = "000300.SH"
BENCHMARK_NAME = "沪深300"


@dataclass(frozen=True, slots=True)
class AccountStart:
    open_date: date
    initial_capital: float


@dataclass(frozen=True, slots=True)
class DayFill:
    trade_date: date
    symbol: str
    stock_name: str
    direction: str
    quantity: int
    price: float
