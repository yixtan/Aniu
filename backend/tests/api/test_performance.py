"""API test for the return curve endpoint."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from backend.api.deps import get_performance_service
from backend.business.performance import DayFill, PerformanceService
from backend.business.performance.models import EquityRecordingStatus
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


class RecordingStatus:
    async def recording_status(self) -> EquityRecordingStatus:
        return EquityRecordingStatus(
            trade_date=OPEN,
            attempted_at=datetime(2026, 8, 28, 7, 0, tzinfo=UTC),
            last_success_at=None,
            error_message="数据库正忙，请稍后刷新账户。",
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
        recording_status=RecordingStatus(),
    )

    response = await api_client.get("/api/aniu/performance/return-curve")

    assert response.status_code == 200
    body = response.json()
    assert body["benchmark_name"] == "沪深300"
    assert body["initial_capital"] == 1_000_000.0
    assert body["base_date"] == "2026-08-27"
    assert body["base_benchmark_close"] == 4630.28
    for _ in range(2):
        reread = await api_client.get("/api/aniu/performance/return-curve")
        assert reread.json()["recording_status"] == {
            "trade_date": "2026-08-28",
            "attempted_at": "2026-08-28T07:00:00Z",
            "last_success_at": None,
            "error_message": "数据库正忙，请稍后刷新账户。",
        }
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
