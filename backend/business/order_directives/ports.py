"""Ports for the order directives feature."""

from __future__ import annotations

from typing import Protocol

from backend.business.order_directives.models import OrderDirective


class OrderDirectiveRepositoryPort(Protocol):
    async def replace_all(
        self, *, run_id: int, directives: list[OrderDirective]
    ) -> None:
        """Swap the whole list for this run's.

        Wholesale replacement is what gives a directive its lifetime: nothing
        expires on a timer, it simply stops being the current list when the next
        run states its own. An authority nobody restates does not survive.
        """
        ...

    async def list_current(self) -> list[OrderDirective]: ...

    async def record_reprice(self, *, order_id: str) -> None:
        """Count one reprice against the plan's ``max_times`` budget."""
        ...


__all__ = ["OrderDirectiveRepositoryPort"]
