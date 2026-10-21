"""Return curve API response schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from backend.api.schemas.common import ApiModel


class ReturnCurveFillResponse(ApiModel):
    symbol: str
    stock_name: str
    direction: str
    quantity: int
    price: float


class ReturnCurveDayResponse(ApiModel):
    date: date
    total_asset: float
    market_value: float
    source: Literal["recorded", "reconstructed"]
    is_close: bool
    benchmark_close: float | None = None
    fills: list[ReturnCurveFillResponse]


class EquityRecordingStatusResponse(ApiModel):
    trade_date: date
    attempted_at: datetime
    last_success_at: datetime | None
    error_message: str | None


class ReturnCurveResponse(ApiModel):
    benchmark_name: str
    initial_capital: float | None = None
    open_date: date | None = None
    base_date: date | None = None
    base_benchmark_close: float | None = None
    days: list[ReturnCurveDayResponse]
    recording_status: EquityRecordingStatusResponse | None = None
