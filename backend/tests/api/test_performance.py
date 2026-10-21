"""API test for the return curve endpoint."""

from __future__ import annotations

from datetime import date

import pytest

from backend.api.deps import get_performance_service
from backend.business.performance import DayFill, PerformanceService
from backend.main import app
from backend.tests.business.test_performance_service import (
    INDEX,
    OPEN,
    BenchmarkRepo,
    Calendar,
    EquityRepo,
    Facts,
    History,
    _day,
    _shanghai,
)


@pytest.mark.asyncio
async def test_the_return_curve_is_served_with_its_base_and_fills(api_client) -> None:
    app.dependency_overrides[get_performance_service] = lambda: PerformanceService(
        equity_repo=EquityRepo(
            [_day(OPEN, 1_000_000.0), _day(date(2026, 8, 31), 1_008_036.0)]
        ),
        benchmark_repo=BenchmarkRepo(),
        benchmark_history=History(INDEX),
        account_facts=Facts(
            fills=[DayFill(date(2026, 8, 31), "600276", "恒瑞医药", "BUY", 400, 45.95)]
        ),
        trading_calendar=Calendar(),
        clock=lambda: _shanghai(date(2026, 9, 1), 10),
    )

    response = await api_client.get("/api/aniu/performance/return-curve")

    assert response.status_code == 200
    body = response.json()
    assert body["benchmark_name"] == "沪深300"
    assert body["initial_capital"] == 1_000_000.0
    assert body["base_date"] == "2026-08-27"
    assert body["base_benchmark_close"] == 4630.28
    assert body["days"][1] == {
        "date": "2026-08-31",
        "total_asset": 1_008_036.0,
        "market_value": 50_000.0,
        "source": "recorded",
        "is_close": True,
        "benchmark_close": 4620.0,
        "fills": [
            {
                "symbol": "600276",
                "stock_name": "恒瑞医药",
                "direction": "BUY",
                "quantity": 400,
                "price": 45.95,
            }
        ],
    }
