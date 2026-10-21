"""The return curve as the dashboard reads it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from backend.business.performance.models import EquityRecordingStatus


@dataclass(frozen=True, slots=True)
class ReturnCurveFillDTO:
    symbol: str
    stock_name: str
    direction: str
    quantity: int
    price: float


@dataclass(frozen=True, slots=True)
class ReturnCurveDayDTO:
    date: date
    total_asset: float
    market_value: float
    source: str
    # False for a day still trading, or one Aniu stopped recording before
    # the close: its value is a moment of the day, not where it ended.
    is_close: bool
    benchmark_close: float | None
    fills: list[ReturnCurveFillDTO] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReturnCurveDTO:
    benchmark_name: str
    initial_capital: float | None
    open_date: date | None
    # The trading day before the account opened: where both lines start from.
    base_date: date | None
    base_benchmark_close: float | None
    days: list[ReturnCurveDayDTO]
    recording_status: EquityRecordingStatus | None = None
