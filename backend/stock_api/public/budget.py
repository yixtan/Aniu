"""How long the operation in progress may still run, for multi-request adapters.

The router bounds each candidate with ``asyncio.timeout``. When that fires,
the adapter's await is cancelled and whatever it had fetched is lost with it.
An adapter that can keep a partial answer — the 龙虎榜 listings without their
seats — has to stop by itself a little before, so the router publishes the
candidate's deadline here and the adapter reads it.

A leaf module: the router and the providers both import it, and it imports
neither.
"""

from __future__ import annotations

import time
from contextvars import ContextVar

operation_deadline: ContextVar[float | None] = ContextVar(
    "public_stock_operation_deadline", default=None
)
"""``time.monotonic()`` value at which the router gives up on this candidate."""


def time_left() -> float | None:
    """Seconds until the router gives up, or None outside a routed call."""

    deadline = operation_deadline.get()
    return None if deadline is None else deadline - time.monotonic()


__all__ = ["operation_deadline", "time_left"]
