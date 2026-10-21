"""Working out past closing values from the fills, for days nobody recorded.

The broker keeps every order but no history of the account's value, so a day
Aniu did not record can only be rebuilt: replay the fills in order to know
what was held and how much cash was left after each day, then price the
holdings at that day's close.

Cash is where it stops being exact. Every fill also paid commission, stamp
duty and transfer fees that no record itemises. The standard A-share schedule
accounts for most of it but not all, so its total is scaled to the fees the
account actually paid — the gap between the cash the fills alone would leave
and the cash the broker reports. On the 2026-10 data that put two recorded
closes within 50 yuan of the reconstruction, about 0.005% of the account.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime

from backend.business.performance.models import DailyEquity, DayFill, EquitySource

COMMISSION_RATE = 0.0003
MINIMUM_COMMISSION = 5.0
STAMP_DUTY_RATE = 0.001  # sells only
TRANSFER_FEE_RATE = 0.00002


def standard_fee(fill: DayFill) -> float:
    amount = fill.quantity * fill.price
    stamp = amount * STAMP_DUTY_RATE if fill.direction == "SELL" else 0.0
    return (
        max(amount * COMMISSION_RATE, MINIMUM_COMMISSION)
        + amount * TRANSFER_FEE_RATE
        + stamp
    )


def cash_from_fills(initial_capital: float, fills: Iterable[DayFill]) -> float:
    """The cash the fills alone would leave, before any fee."""

    cash = initial_capital
    for fill in fills:
        amount = fill.quantity * fill.price
        cash += amount if fill.direction == "SELL" else -amount
    return cash


def fee_scale(
    fills: list[DayFill], *, initial_capital: float, actual_cash: float
) -> float:
    """How far the standard schedule must be scaled to match the fees paid.

    `actual_cash` is the broker's available plus frozen cash: money set aside
    for a resting order has not left the account.
    """

    modelled = sum(standard_fee(fill) for fill in fills)
    if modelled <= 0:
        return 1.0
    paid = cash_from_fills(initial_capital, fills) - actual_cash
    return paid / modelled


def holdings_after(fills: Iterable[DayFill]) -> dict[str, int]:
    held: Counter[str] = Counter()
    for fill in fills:
        held[fill.symbol] += (
            fill.quantity if fill.direction == "BUY" else -fill.quantity
        )
    return {symbol: quantity for symbol, quantity in held.items() if quantity}


@dataclass(frozen=True, slots=True)
class MissingClose:
    symbol: str
    trade_date: date


def reconstruct(
    days: list[date],
    fills: list[DayFill],
    closes: Mapping[str, Mapping[date, float]],
    *,
    initial_capital: float,
    scale: float,
    captured_at: datetime,
) -> tuple[list[DailyEquity], list[MissingClose]]:
    """Each day's closing value, with the holdings priced at that day's close.

    A holding without a close that day — suspended, or missing upstream — is
    priced at its last known close and reported, so the caller can decide
    whether the result is still good enough to keep.
    """

    by_day: dict[date, list[DayFill]] = {}
    for fill in fills:
        by_day.setdefault(fill.trade_date, []).append(fill)
    cash = initial_capital
    held: Counter[str] = Counter()
    last_close: dict[str, float] = {}
    missing: list[MissingClose] = []
    values: list[DailyEquity] = []
    for day in sorted(days):
        for fill in by_day.get(day, []):
            amount = fill.quantity * fill.price
            fee = standard_fee(fill) * scale
            if fill.direction == "BUY":
                cash -= amount + fee
                held[fill.symbol] += fill.quantity
            else:
                cash += amount - fee
                held[fill.symbol] -= fill.quantity
            last_close.setdefault(fill.symbol, fill.price)
        market_value = 0.0
        for symbol, quantity in held.items():
            if not quantity:
                continue
            close = closes.get(symbol, {}).get(day)
            if close is None:
                missing.append(MissingClose(symbol, day))
                close = last_close[symbol]
            last_close[symbol] = close
            market_value += quantity * close
        values.append(
            DailyEquity(
                trade_date=day,
                total_asset=round(cash + market_value, 2),
                available_cash=round(cash, 2),
                frozen_cash=0.0,
                market_value=round(market_value, 2),
                captured_at=captured_at,
                source=EquitySource.RECONSTRUCTED,
            )
        )
    return values, missing


__all__ = [
    "MissingClose",
    "cash_from_fills",
    "fee_scale",
    "holdings_after",
    "reconstruct",
    "standard_fee",
]
