"""Rebuilding past closing values from the fills."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from backend.business.performance import DayFill, EquitySource
from backend.business.performance.reconstruction import (
    MissingClose,
    fee_scale,
    holdings_after,
    reconstruct,
    standard_fee,
)

D1, D2, D3 = date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12)
NOW = datetime(2026, 10, 21, 8, tzinfo=UTC)


def _fill(day: date, direction: str, quantity: int, price: float, symbol="600276"):
    return DayFill(day, symbol, "恒瑞医药", direction, quantity, price)


def test_the_standard_schedule_charges_stamp_duty_on_sells_only() -> None:
    buy = _fill(D1, "BUY", 400, 50.0)  # 20,000
    sell = _fill(D1, "SELL", 400, 50.0)

    assert standard_fee(buy) == pytest.approx(6.0 + 0.4)
    assert standard_fee(sell) == pytest.approx(6.0 + 0.4 + 20.0)


def test_a_small_order_pays_the_minimum_commission() -> None:
    assert standard_fee(_fill(D1, "BUY", 100, 10.0)) == pytest.approx(5.0 + 0.02)


def test_the_scale_matches_the_fees_the_account_actually_paid() -> None:
    fills = [_fill(D1, "BUY", 400, 50.0), _fill(D2, "SELL", 400, 52.0)]
    no_fee_cash = 1_000_000 - 20_000 + 20_800
    modelled = sum(standard_fee(fill) for fill in fills)

    scale = fee_scale(
        fills, initial_capital=1_000_000, actual_cash=no_fee_cash - 1.1 * modelled
    )

    assert scale == pytest.approx(1.1)


def test_holdings_are_what_the_fills_leave() -> None:
    fills = [
        _fill(D1, "BUY", 400, 50.0),
        _fill(D1, "BUY", 600, 39.0, symbol="600150"),
        _fill(D2, "SELL", 400, 52.0),
    ]

    assert holdings_after(fills) == {"600150": 600}


def test_each_day_prices_what_was_held_at_that_days_close() -> None:
    fills = [_fill(D1, "BUY", 400, 50.0), _fill(D3, "SELL", 400, 53.0)]
    closes = {"600276": {D1: 51.0, D2: 52.0, D3: 53.5}}

    values, missing = reconstruct(
        [D1, D2, D3],
        fills,
        closes,
        initial_capital=1_000_000,
        scale=1.0,
        captured_at=NOW,
    )

    buy_fee = standard_fee(fills[0])
    sell_fee = standard_fee(fills[1])
    assert missing == []
    assert [value.market_value for value in values] == [20_400.0, 20_800.0, 0.0]
    assert values[0].total_asset == pytest.approx(
        1_000_000 - 20_000 - buy_fee + 20_400, abs=0.01
    )
    assert values[2].total_asset == pytest.approx(
        1_000_000 - 20_000 - buy_fee + 21_200 - sell_fee, abs=0.01
    )
    assert {value.source for value in values} == {EquitySource.RECONSTRUCTED}


def test_a_missing_close_carries_the_last_one_and_is_reported() -> None:
    fills = [_fill(D1, "BUY", 400, 50.0)]
    closes = {"600276": {D1: 51.0, D3: 52.0}}  # suspended on D2

    values, missing = reconstruct(
        [D1, D2, D3],
        fills,
        closes,
        initial_capital=1_000_000,
        scale=1.0,
        captured_at=NOW,
    )

    assert missing == [MissingClose("600276", D2)]
    assert values[1].market_value == 20_400.0


def test_days_before_the_first_fill_are_all_cash() -> None:
    values, _ = reconstruct(
        [date(2026, 8, 28), D1],
        [_fill(D1, "BUY", 400, 50.0)],
        {"600276": {D1: 50.0}},
        initial_capital=1_000_000,
        scale=1.0,
        captured_at=NOW,
    )

    assert values[0].total_asset == 1_000_000
    assert values[0].market_value == 0
