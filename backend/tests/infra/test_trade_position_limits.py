"""Regression for concentration, symbol count, unknown orders and T+1."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from backend.business.account import PositionSnapshot
from backend.infra.integrations.exposure_trade_guard import (
    check_buy_exposure,
    check_position_limits,
)
from backend.infra.integrations.mx_agent_tools import TradeTool
from backend.tests.infra.test_exposure_execution_guard import (
    clients,
    declare,
    order,
    seed,
    snapshot,
)


@pytest.fixture(autouse=True)
def execution_clock(monkeypatch):
    monkeypatch.setattr(
        "backend.infra.integrations.mx_agent_tools.utc_now",
        lambda: datetime(2026, 10, 23, 2, tzinfo=UTC),
    )
    monkeypatch.setattr(
        "backend.infra.integrations.exposure_trade_guard.utc_now",
        lambda: datetime(2026, 10, 23, 2, tzinfo=UTC),
    )


def holding(code="600000", value=9500, available=100):
    return PositionSnapshot(
        symbol=code,
        stock_name="合成测试",
        quantity=100,
        avg_cost=10,
        current_price=10,
        market_value=value,
        profit_ratio=0,
        available_quantity=available,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
async def test_single_stock_cap_blocks_before_client(session_factory, pending):
    await seed(session_factory, 1)
    await declare(session_factory, 1, 30)
    account = replace(snapshot(), market_value=7500 if pending else 9500, frozen_cash=0)
    orders = [order(order_price=20)] if pending else []
    p, t = clients(account, orders)
    p.get_positions.return_value = [holding(value=account.market_value)]
    with pytest.raises(ValueError, match="单票"):
        await TradeTool(t, p, session_factory=session_factory).run_for_call(
            run_id=1, tool_call_id="buy", instruction="买入 600000 10 100"
        )
    t.trade.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("held", [0, 5, 10])
async def test_pending_and_held_names_both_count_towards_ten(session_factory, held):
    await seed(session_factory, 1)
    await declare(session_factory, 1, 30)
    p, t = clients(
        replace(snapshot(), market_value=held * 1000, frozen_cash=0),
        [
            order(order_id=f"pending-{i}", symbol=f"600{i:03d}", order_price=10)
            for i in range(held, 10)
        ],
    )
    p.get_positions.return_value = [holding(f"600{i:03d}", 1000) for i in range(held)]
    with pytest.raises(ValueError, match="超过10只"):
        await TradeTool(t, p, session_factory=session_factory).run_for_call(
            run_id=1, tool_call_id="buy", instruction="买入 600010 10 100"
        )
    t.trade.assert_not_awaited()


def test_partial_fill_deduplicated_and_cancelled_order_excluded():
    account = replace(snapshot(), market_value=5000, frozen_cash=0)
    positions = [holding(f"600{i:03d}", 500) for i in range(10)]
    orders = [
        order(
            symbol="600000",
            quantity=100,
            filled_quantity=50,
            filled_price=10,
            order_price=10,
            status="PARTIAL",
        ),
        order(symbol="600010", status="CANCELLED"),
    ]
    check_position_limits(
        account, positions, orders, symbol="600000", price=10, quantity=100
    )


def test_single_stock_exact_boundary_and_unrelated_overweight():
    account = replace(snapshot(), market_value=25000, frozen_cash=0)
    positions = [holding(value=9000), holding("600001", 16000)]
    check_position_limits(
        account, positions, [], symbol="600000", price=10, quantity=100
    )
    with pytest.raises(ValueError, match="单票"):
        check_position_limits(
            account, positions, [], symbol="600001", price=1, quantity=100
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["PENDING", "UNKNOWN", "PARTIAL", "PENDING_CANCEL"])
async def test_unknown_direction_never_disappears_from_preflight(
    session_factory, status
):
    await seed(session_factory, 1)
    await declare(session_factory, 1, 8)
    p, t = clients(
        replace(snapshot(), market_value=0, frozen_cash=0),
        [order(direction="UNKNOWN", status=status)],
    )
    with pytest.raises(ValueError, match="方向不明"):
        await TradeTool(t, p, session_factory=session_factory).run_for_call(
            run_id=1, tool_call_id="buy", instruction="买入 600002 1 100"
        )
    t.trade.assert_not_awaited()


@pytest.mark.parametrize(
    "status", ["CANCELLED", "PARTIAL_CANCELLED", "REJECTED", "FILLED"]
)
def test_confirmed_terminal_unknown_direction_has_no_new_risk(status):
    check_buy_exposure(
        replace(snapshot(), market_value=0, frozen_cash=0),
        [order(direction="UNKNOWN", status=status)],
        price=1,
        quantity=100,
        limit=8,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [None, 0, 50])
async def test_missing_or_insufficient_sellable_quantity_blocks_sale(available):
    p, t = clients(
        replace(snapshot(), market_value=1000),
        [order(status="FILLED", filled_quantity=100, filled_price=10)],
    )
    p.get_positions.return_value = [holding(value=1000, available=available)]
    with pytest.raises(ValueError, match="可卖数量"):
        await TradeTool(t, p).run_for_call(
            run_id=1, tool_call_id="sell", instruction="卖出 600000 10 100"
        )
    t.trade.assert_not_awaited()
    p.clear_read_cache.assert_called_once()


@pytest.mark.asyncio
async def test_confirmed_sellable_shares_can_exit_without_cap():
    p, t = clients(replace(snapshot(), market_value=1000))
    p.get_positions.return_value = [holding(value=1000, available=100)]
    await TradeTool(t, p).run_for_call(
        run_id=1, tool_call_id="sell", instruction="卖出 600000 10 100"
    )
    t.trade.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_positions_prevents_concentration_bypass(session_factory):
    await seed(session_factory, 1)
    await declare(session_factory, 1, 30)
    p, t = clients(replace(snapshot(), market_value=5000, frozen_cash=0))
    p.get_positions.return_value = []
    with pytest.raises(ValueError, match="缺少持仓明细"):
        await TradeTool(t, p, session_factory=session_factory).run_for_call(
            run_id=1, tool_call_id="buy", instruction="买入 600000 10 100"
        )
    t.trade.assert_not_awaited()


@pytest.mark.asyncio
async def test_upstream_available_does_not_override_today_buy_lock():
    p, t = clients(
        replace(snapshot(), market_value=1000),
        [order(status="FILLED", filled_quantity=100, filled_price=10)],
    )
    p.get_positions.return_value = [holding(value=1000, available=100)]
    with pytest.raises(ValueError, match="可卖数量 0"):
        await TradeTool(t, p).run_for_call(
            run_id=1, tool_call_id="sell", instruction="卖出 600000 10 100"
        )
    t.trade.assert_not_awaited()


@pytest.mark.asyncio
async def test_mixed_old_and_today_holdings_only_sell_settled_remainder():
    p, t = clients(
        replace(snapshot(), market_value=5000),
        [order(status="FILLED", filled_quantity=100, filled_price=10)],
    )
    p.get_positions.return_value = [
        replace(holding(value=5000, available=500), quantity=500)
    ]
    await TradeTool(t, p).run_for_call(
        run_id=1, tool_call_id="sell", instruction="卖出 600000 10 400"
    )
    t.trade.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_sell_reserves_settled_shares_without_double_deduct():
    p, t = clients(
        replace(snapshot(), market_value=5000),
        [order(direction="SELL", quantity=100, order_price=10)],
    )
    p.get_positions.return_value = [
        replace(holding(value=5000, available=400), quantity=500)
    ]
    await TradeTool(t, p).run_for_call(
        run_id=1, tool_call_id="sell", instruction="卖出 600000 10 400"
    )
    t.trade.assert_awaited_once()


@pytest.mark.asyncio
async def test_filled_buy_without_time_blocks_sale():
    p, t = clients(
        replace(snapshot(), market_value=1000),
        [
            order(
                status="FILLED", filled_quantity=100, filled_price=10, submitted_at=None
            )
        ],
    )
    p.get_positions.return_value = [holding(value=1000, available=100)]
    with pytest.raises(ValueError, match="成交时间缺失"):
        await TradeTool(t, p).run_for_call(
            run_id=1, tool_call_id="sell", instruction="卖出 600000 10 100"
        )
    t.trade.assert_not_awaited()
