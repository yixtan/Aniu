"""Validate limit buys against their declared invalidation range."""

from __future__ import annotations

import math


def _positive(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError("买入失效条件必须是有效正数")
    return float(value)


def check_buy_conditions(
    price: float, *, below: object = None, above: object = None
) -> None:
    """A limit buy must be inside the price range where its thesis is valid."""
    price = _positive(price)
    if below is not None and price <= _positive(below):
        raise ValueError("买价必须高于撤单失效下界，禁止在逻辑失效价位挂买单")
    if above is not None and price >= _positive(above):
        raise ValueError("买价必须低于撤单失效上界，禁止矛盾的买入计划")
