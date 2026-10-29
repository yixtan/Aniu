"""Real MX adapters with in-memory HTTP: disallowed writes never leave."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.business.order_directives import DirectiveAction, OrderDirective
from backend.infra.integrations.mx_agent_tools import CancelTool, TradeTool
from backend.stock_api.mx import MxMoniClient, MxPaperTradingClient
from backend.stock_api.mx.http import MxHttpTransport
from backend.tests.infra.buy_fixtures import approve
from backend.tests.infra.test_exposure_execution_guard import declare, seed


@pytest.fixture(autouse=True)
def clocks(monkeypatch):
    now = datetime(2026, 10, 23, 2, tzinfo=UTC)
    monkeypatch.setattr(
        "backend.infra.integrations.mx_agent_tools.utc_now",
        lambda: datetime(2026, 10, 23, 2, tzinfo=UTC),
    )
    monkeypatch.setattr(
        "backend.infra.integrations.exposure_trade_guard.utc_now", lambda: now
    )
    monkeypatch.setattr(
        "backend.infra.integrations.watch_cancel_guard.utc_now", lambda: now
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "overweight",
        "eleventh",
        "unknown-direction",
        "missing-sellable",
        "missing-review",
        "valid-buy",
        "valid-sell",
    ],
)
async def test_real_adapters_enforce_before_trade_http(session_factory, scenario):
    await seed(session_factory, 1)
    await declare(session_factory, 1, 30)
    writes = []
    position = {
        "stockCode": "600000",
        "stockName": "合成测试",
        "quantity": 100,
        "marketValue": 9500 if scenario == "overweight" else 1000,
        "availCount": 100,
    }
    if scenario == "missing-sellable":
        position.pop("availCount")
    positions = [position]
    if scenario == "eleventh":
        positions = [
            {**position, "stockCode": f"600{i:03d}", "marketValue": 1000}
            for i in range(10)
        ]
    orders = []
    if scenario == "unknown-direction":
        orders = [
            {
                "orderId": "unknown",
                "stockCode": "600002",
                "stockName": "合成测试",
                "direction": "UNKNOWN",
                "quantity": 100,
                "orderPrice": 10,
                "status": "PENDING",
                "filledQuantity": 0,
            }
        ]

    def handle(request):
        path = request.url.path
        if path.endswith("/balance"):
            data = {
                "totalAssets": 100000,
                "availBalance": 90000,
                "totalPosValue": sum(p["marketValue"] for p in positions),
                "frozenMoney": 0,
                "currencyUnit": 1,
            }
        elif path.endswith("/positions"):
            data = {"posList": positions, "currencyUnit": 1}
        elif path.endswith("/orders"):
            data = {"orders": orders}
        elif path.endswith("/trade"):
            writes.append(path)
            data = {"orderId": "synthetic"}
        else:
            raise AssertionError(path)
        return httpx.Response(200, json={"code": 200, "data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        p = MxMoniClient(
            api_key="isolated-test",
            base_url="https://isolated.invalid",
            http_client=http,
            request_interval=0,
        )
        t = MxPaperTradingClient(
            MxHttpTransport(
                api_key="isolated-test",
                base_url="https://isolated.invalid",
                http_client=http,
                request_interval=0,
            ),
            read_cache=p.read_cache,
        )
        tool = TradeTool(t, p, session_factory=session_factory)
        instruction = (
            "卖出 600000 10 100"
            if scenario in {"missing-sellable", "valid-sell"}
            else "买入 600010 10 100"
            if scenario == "eleventh"
            else "买入 600000 10 100"
        )
        if scenario.startswith("valid"):
            if scenario == "valid-buy":
                tool.market_data = await approve(session_factory, 1, instruction)
            await tool.run_for_call(
                run_id=1,
                tool_call_id="write",
                buy_review_call_id="review",
                instruction=instruction,
            )
            assert len(writes) == 1
        else:
            with pytest.raises(ValueError):
                await tool.run_for_call(
                    run_id=1,
                    tool_call_id="write",
                    buy_review_call_id="review",
                    instruction=instruction,
                )
            assert writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("triggered", [False, True])
async def test_cancel_http_respects_real_price_gate(triggered):
    writes = []

    def handle(request):
        if request.url.path.endswith("/orders"):
            data = {
                "orders": [
                    {
                        "orderId": "test",
                        "stockCode": "600000",
                        "stockName": "合成测试",
                        "direction": "BUY",
                        "quantity": 100,
                        "orderPrice": 10,
                        "status": "PENDING",
                        "filledQuantity": 0,
                    }
                ]
            }
        elif request.url.path.endswith("/cancel"):
            writes.append(request.url.path)
            data = {"status": "cancelled"}
        else:
            raise AssertionError(request.url.path)
        return httpx.Response(200, json={"code": 200, "data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        p = MxMoniClient(
            api_key="isolated-test",
            base_url="https://isolated.invalid",
            http_client=http,
            request_interval=0,
        )
        t = MxPaperTradingClient(
            MxHttpTransport(
                api_key="isolated-test",
                base_url="https://isolated.invalid",
                http_client=http,
                request_interval=0,
            )
        )
        market = AsyncMock()
        market.execute_fresh.return_value = {
            "data": {
                "quotes": [
                    {
                        "symbol": "600000.SH",
                        "price": 10,
                        "market_time": "2026-10-23T02:00:00+00:00",
                    }
                ]
            }
        }
        tool = CancelTool(t, p, market_data=market)
        plan = OrderDirective(
            order_id="test",
            symbol="600000",
            stock_name="合成测试",
            action=DirectiveAction.HOLD,
            note="合成",
            issued_by_run_id=1,
            cancel_if_price_above=9 if triggered else 11,
        )
        if triggered:
            await tool.run_for_watch(
                instruction="撤单 test 600000",
                directives=(plan,),
                cancellations_accepted=lambda: True,
            )
            assert len(writes) == 1
        else:
            with pytest.raises(ValueError):
                await tool.run_for_watch(
                    instruction="撤单 test 600000",
                    directives=(plan,),
                    cancellations_accepted=lambda: True,
                )
            assert writes == []
