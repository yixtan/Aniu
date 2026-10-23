"""A simulator spy verifies the incident is stopped before any write request."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

from backend.business.account import AccountSnapshot, PortfolioOrderSnapshot
from backend.infra.db.models import ExposureCapModel, StrategyRunModel
from backend.infra.integrations.exposure_cap_agent_tools import DeclareExposureCapTool
from backend.infra.integrations.exposure_trade_guard import check_buy_exposure
from backend.infra.integrations.mx_agent_tools import TradeTool

REASONS = dict(basis="市场结构", changed_from="按当日口径", forgone="候补")
NOW = datetime(2026, 10, 23, 6, 40, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(
        "backend.infra.integrations.exposure_trade_guard.utc_now", lambda: NOW
    )


async def seed(factory, run_id, started="2026-10-23T01:30:00+00:00"):
    async with factory() as session:
        session.add(
            StrategyRunModel(
                id=run_id,
                trigger_source="SCHEDULED",
                status="COMPLETED",
                current_state="Completed",
                snapshot_json={},
                trace_json={},
                summary_render_mode="markdown",
                started_at=started,
            )
        )
        await session.commit()


async def declare(factory, run_id, pct):
    return await DeclareExposureCapTool(factory).run_for_call(
        run_id=run_id, tool_call_id="cap", cap_pct=pct, **REASONS
    )


def snapshot():
    return AccountSnapshot(
        total_asset=100000,
        available_cash=90000,
        frozen_cash=4350,
        market_value=3460,
        total_profit=0,
        daily_profit=0,
    )


def order(**kwargs):
    return PortfolioOrderSnapshot(
        **{
            "order_id": "test",
            "symbol": "600000",
            "stock_name": "测试股票",
            "direction": "BUY",
            "quantity": 100,
            "status": "PENDING",
            "filled_quantity": 0,
            "filled_price": None,
            "order_price": 20,
            "submitted_at": datetime(2026, 10, 23, 1, tzinfo=UTC),
            **kwargs,
        }
    )


def clients(account=None, orders=None):
    portfolio = AsyncMock()
    portfolio.clear_read_cache = Mock()
    portfolio.get_account_snapshot.return_value = account or snapshot()
    portfolio.get_orders.return_value = [] if orders is None else orders
    trading = AsyncMock()
    trading.trade.return_value = {"status": "submitted"}
    return portfolio, trading


@pytest.mark.asyncio
async def test_109_cannot_raise_then_buy_even_after_redeclaring_eight(session_factory):
    await seed(session_factory, 108)
    await seed(session_factory, 109)
    await declare(session_factory, 108, 8)
    with pytest.raises(ValueError, match="同日重跑"):
        await declare(session_factory, 109, 10)
    portfolio, trading = clients()
    tool = TradeTool(trading, portfolio, session_factory=session_factory)
    with pytest.raises(ValueError, match="尚未声明"):
        await tool.run_for_call(
            run_id=109, tool_call_id="buy", instruction="买入 600000 14.3 100"
        )
    await declare(session_factory, 109, 8)
    with pytest.raises(ValueError, match="潜在总敞口"):
        await tool.run_for_call(
            run_id=109, tool_call_id="buy", instruction="买入 600000 14.3 100"
        )
    trading.trade.assert_not_awaited()
    async with session_factory() as session:
        rows = (await session.scalars(select(ExposureCapModel))).all()
        assert [row.cap_pct for row in rows] == [8, 8]


@pytest.mark.asyncio
async def test_lowering_cannot_be_undone_in_same_run_or_another(session_factory):
    await seed(session_factory, 1)
    await seed(session_factory, 2)
    await declare(session_factory, 1, 8)
    await declare(session_factory, 1, 6)
    for run in (1, 2):
        with pytest.raises(ValueError, match="6%"):
            await declare(session_factory, run, 8)
    await declare(session_factory, 2, 6)


@pytest.mark.asyncio
async def test_new_shanghai_day_can_raise_even_when_utc_day_is_same(session_factory):
    await seed(session_factory, 1, "2026-10-22T15:59:00+00:00")
    # Seed yesterday's declaration directly; execution today cannot alter it.
    from backend.business.exposure import ExposureCap
    from backend.infra.repositories.exposure_cap_repo import ExposureCapRepository

    async with session_factory() as session:
        await ExposureCapRepository(session).declare(
            ExposureCap(run_id=1, cap_pct=8, **REASONS)
        )
        await session.commit()
    await seed(session_factory, 2, "2026-10-22T16:01:00+00:00")
    result = await declare(session_factory, 2, 10)
    assert result["cap_pct"] == 10
    with pytest.raises(ValueError, match="跨交易日"):
        await declare(session_factory, 1, 7)


@pytest.mark.asyncio
async def test_concurrent_declarations_cannot_race_past_lower_limit(session_factory):
    for i in (1, 2, 3):
        await seed(session_factory, i)
    await declare(session_factory, 1, 8)
    results = await asyncio.gather(
        declare(session_factory, 2, 6),
        declare(session_factory, 3, 10),
        return_exceptions=True,
    )
    assert isinstance(results[0], dict)
    assert isinstance(results[1], ValueError)


@pytest.mark.asyncio
async def test_legacy_ten_percent_row_does_not_override_days_eight(session_factory):
    from backend.business.exposure import ExposureCap
    from backend.infra.repositories.exposure_cap_repo import ExposureCapRepository

    for i in (1, 2):
        await seed(session_factory, i)
    await declare(session_factory, 1, 8)
    async with session_factory() as session:
        await ExposureCapRepository(session).declare(
            ExposureCap(run_id=2, cap_pct=10, **REASONS)
        )
        await session.commit()
    portfolio, trading = clients()
    tool = TradeTool(trading, portfolio, session_factory=session_factory)
    with pytest.raises(ValueError, match="8%"):
        await tool.run_for_call(
            run_id=2, tool_call_id="buy", instruction="买入 600000 14.3 100"
        )
    trading.trade.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_buy_passes_and_exits_need_no_cap(session_factory):
    await seed(session_factory, 1)
    await declare(session_factory, 1, 8)
    portfolio, trading = clients(replace(snapshot(), market_value=3000, frozen_cash=0))
    tool = TradeTool(trading, portfolio, session_factory=session_factory)
    assert await tool.run_for_call(
        run_id=1, tool_call_id="buy", instruction="买入 600000 14.3 100"
    ) == {"status": "submitted"}
    trading.trade.assert_awaited_once()
    # Sale still goes through the existing quantity check, never this buy gate.
    portfolio.get_positions.return_value = []
    with pytest.raises(ValueError, match="可卖数量"):
        await tool.run_for_call(
            run_id=999, tool_call_id="sell", instruction="卖出 600000 14.3 100"
        )
    with pytest.raises(RuntimeError, match="trusted"):
        await tool.run("买入 600000 14.3 100")


@pytest.mark.asyncio
async def test_missing_persistence_and_portfolio_errors_fail_closed(session_factory):
    portfolio, trading = clients()
    tool = TradeTool(trading, portfolio)
    with pytest.raises(ValueError, match="持久化"):
        await tool.run_for_call(
            run_id=1, tool_call_id="buy", instruction="买入 600000 14.3 100"
        )
    await seed(session_factory, 1)
    await declare(session_factory, 1, 8)
    tool.session_factory = session_factory
    portfolio.get_orders.side_effect = OSError("offline")
    with pytest.raises(OSError):
        await tool.run_for_call(
            run_id=1, tool_call_id="buy", instruction="买入 600000 14.3 100"
        )
    trading.trade.assert_not_awaited()


def test_pending_buys_count_but_filled_cancelled_and_sells_do_not():
    account = replace(snapshot(), total_asset=100000, market_value=5000, frozen_cash=0)
    # 50 filled shares are included in the holding; 50 pending * 20 = 1000.
    pending = order(
        quantity=100,
        filled_quantity=50,
        filled_price=20,
        order_price=20,
        status="PARTIAL",
    )
    other = [
        order(status="CANCELLED"),
        order(status="PARTIAL_CANCELLED"),
        order(direction="SELL"),
    ]
    check_buy_exposure(account, [pending, *other], price=20, quantity=100, limit=8)
    with pytest.raises(ValueError, match="潜在总敞口"):
        check_buy_exposure(
            account,
            [replace(pending, status="PENDING_CANCEL")],
            price=21,
            quantity=100,
            limit=8,
        )


@pytest.mark.parametrize(
    "bad",
    [order(status="UNKNOWN"), order(order_price=None), order(order_price=float("nan"))],
)
def test_uncertain_pending_orders_block_new_buys(bad):
    with pytest.raises(ValueError, match="不明"):
        check_buy_exposure(snapshot(), [bad], price=1, quantity=100, limit=100)


@pytest.mark.parametrize(
    "field,value",
    [
        ("total_asset", 0),
        ("total_asset", float("nan")),
        ("market_value", -1),
        ("frozen_cash", float("inf")),
    ],
)
def test_invalid_account_blocks_buys(field, value):
    with pytest.raises(ValueError, match="估值无效"):
        check_buy_exposure(
            replace(snapshot(), **{field: value}), [], price=1, quantity=100, limit=100
        )


@pytest.mark.asyncio
async def test_production_factory_wires_guard_and_http_preflight_is_fresh(
    session_factory,
):
    import httpx

    from backend.infra.integrations.agent_runtime import AgentRuntimeFactory
    from backend.stock_api.mx import MxMoniClient, MxPaperTradingClient
    from backend.stock_api.mx.http import MxHttpTransport

    await seed(session_factory, 108)
    await seed(session_factory, 109)
    await declare(session_factory, 108, 8)
    await declare(session_factory, 109, 8)
    calls = []
    state = {"market_value": 0, "frozen": 0}

    def handle(request):
        path = request.url.path
        calls.append(path)
        if path.endswith("/balance"):
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "totalAssets": 100000,
                        "availBalance": 90000,
                        "totalPosValue": state["market_value"],
                        "frozenMoney": state["frozen"],
                        "currencyUnit": 1,
                    },
                },
            )
        if path.endswith("/orders"):
            return httpx.Response(200, json={"code": 200, "data": []})
        if path.endswith("/trade"):
            return httpx.Response(200, json={"code": 200, "data": {"orderId": "test"}})
        raise AssertionError(path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        portfolio = MxMoniClient(
            api_key="isolated-test",
            base_url="https://isolated.invalid",
            http_client=http,
            request_interval=0,
        )
        trading = MxPaperTradingClient(
            MxHttpTransport(
                api_key="isolated-test",
                base_url="https://isolated.invalid",
                http_client=http,
                request_interval=0,
            ),
            read_cache=portfolio.read_cache,
        )
        # Warm cache with a snapshot that would allow buying. The guarded call
        # must fetch the new 7.81% exposure, not approve against this old zero.
        await portfolio.get_account_snapshot()
        state.update(market_value=3460, frozen=4350)
        registry = await AgentRuntimeFactory(
            mx_research_client=object(),
            mx_portfolio_client=portfolio,
            mx_trading_client=trading,
            session_factory=session_factory,
        ).build_tool_registry()
        tool = registry.get("trade")
        with pytest.raises(ValueError, match="8%"):
            await tool.run_for_call(
                run_id=109, tool_call_id="buy", instruction="买入 600000 14.3 100"
            )
        assert sum(p.endswith("/balance") for p in calls) == 2
        assert not any(p.endswith("/trade") for p in calls)
        state.update(market_value=0, frozen=0)
        await tool.run_for_call(
            run_id=109, tool_call_id="buy", instruction="买入 600000 14.3 100"
        )
        assert sum(p.endswith("/trade") for p in calls) == 1


@pytest.mark.asyncio
async def test_concurrent_buys_observe_previous_accepted_reservation(session_factory):
    await seed(session_factory, 1)
    await seed(session_factory, 2)
    await declare(session_factory, 1, 8)
    await declare(session_factory, 2, 8)
    portfolio, trading = clients(
        replace(snapshot(), total_asset=100000, market_value=5000, frozen_cash=0)
    )
    state = {"frozen": 0}

    async def fresh():
        return replace(
            snapshot(),
            total_asset=100000,
            market_value=5000,
            frozen_cash=state["frozen"],
        )

    async def reserve(instruction):
        await asyncio.sleep(0.01)
        state["frozen"] += 2000
        return {"status": "submitted"}

    portfolio.get_account_snapshot.side_effect = fresh
    trading.trade.side_effect = reserve
    results = await asyncio.gather(
        *[
            TradeTool(trading, portfolio, session_factory=session_factory).run_for_call(
                run_id=run_id, tool_call_id="buy", instruction="买入 600000 20 100"
            )
            for run_id in (1, 2)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, ValueError) for r in results) == 1
    trading.trade.assert_awaited_once()


@pytest.mark.asyncio
async def test_chasing_earlier_bid_is_blocked_even_with_plenty_of_cap(session_factory):
    await seed(session_factory, 1, "2026-10-23T06:40:00+00:00")
    await declare(session_factory, 1, 20)
    portfolio, trading = clients(
        replace(snapshot(), market_value=1000, frozen_cash=0), [order(order_price=10)]
    )
    tool = TradeTool(trading, portfolio, session_factory=session_factory)
    with pytest.raises(ValueError, match="高价追买"):
        await tool.run_for_call(
            run_id=1, tool_call_id="buy", instruction="买入 600000 14.3 100"
        )
    trading.trade.assert_not_awaited()
    await tool.run_for_call(
        run_id=1, tool_call_id="lower", instruction="买入 600000 9 100"
    )
    trading.trade.assert_awaited_once()


@pytest.mark.parametrize(
    "status", ["PENDING", "FILLED", "CANCELLED", "PARTIAL_CANCELLED"]
)
def test_cancel_or_fill_does_not_erase_prior_run_price_anchor(status):
    from backend.infra.integrations.exposure_trade_guard import check_same_day_chase

    with pytest.raises(ValueError, match="高价追买"):
        check_same_day_chase(
            [order(order_price=10, status=status)],
            started_at=NOW,
            symbol="600000",
            price=11,
        )


@pytest.mark.parametrize("submitted", [datetime(2026, 10, 22, 1, tzinfo=UTC), NOW])
def test_prior_day_or_current_run_levels_do_not_block_buy(submitted):
    from backend.infra.integrations.exposure_trade_guard import check_same_day_chase

    check_same_day_chase(
        [order(order_price=10, submitted_at=submitted)],
        started_at=NOW,
        symbol="600000",
        price=11,
    )


def test_missing_buy_timestamp_fails_closed():
    from backend.infra.integrations.exposure_trade_guard import check_same_day_chase

    with pytest.raises(ValueError, match="缺少时间"):
        check_same_day_chase(
            [order(submitted_at=None)], started_at=NOW, symbol="600000", price=11
        )
