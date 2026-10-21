"""Account returns use the same unrounded basis as the return curve."""

from __future__ import annotations

import pytest

from backend.business.account import AccountSnapshot
from backend.business.account.dto import to_account_overview_dto


def _snapshot(
    initial_capital: float | None, net_value: float | None
) -> AccountSnapshot:
    return AccountSnapshot(
        total_asset=102_349.0,
        available_cash=82_349.0,
        frozen_cash=0.0,
        market_value=20_000.0,
        total_profit=2_349.0,
        daily_profit=100.0,
        initial_capital=initial_capital,
        net_value=net_value,
    )


def test_rounded_platform_net_value_does_not_change_the_displayed_return() -> None:
    overview = to_account_overview_dto(_snapshot(100_000.0, 1.023))

    assert overview.total_return_rate == pytest.approx(0.02349)
    assert overview.current_net_value == pytest.approx(1.02349)
    assert overview.net_value == 1.023


@pytest.mark.parametrize("initial_capital", [None, 0.0, -100.0])
def test_without_valid_opening_capital_the_platform_value_is_used(
    initial_capital: float | None,
) -> None:
    overview = to_account_overview_dto(_snapshot(initial_capital, 1.023))

    assert overview.total_return_rate == pytest.approx(0.023)


def test_without_either_basis_the_return_is_unavailable() -> None:
    overview = to_account_overview_dto(_snapshot(None, None))

    assert overview.total_return_rate is None
    assert overview.current_net_value is None
