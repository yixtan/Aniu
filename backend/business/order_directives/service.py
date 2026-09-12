"""Recording and reading the current order directives."""

from __future__ import annotations

from dataclasses import dataclass

from backend.business.order_directives.models import DirectiveAction, OrderDirective
from backend.business.order_directives.parsing import parse_directives
from backend.business.order_directives.ports import OrderDirectiveRepositoryPort


@dataclass(frozen=True, slots=True)
class DirectiveRecordResult:
    accepted: int
    downgraded: int
    """Entries kept as HOLD because some field could not be read."""
    unfilable: tuple[str, ...]
    """Entries dropped outright, having no order id to file them against."""

    @property
    def as_payload(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "downgraded": self.downgraded,
            "unfilable": list(self.unfilable),
        }


class OrderDirectiveService:
    def __init__(self, repository: OrderDirectiveRepositoryPort) -> None:
        self._repository = repository

    async def record(
        self, payloads: list[dict[str, object]], *, run_id: int
    ) -> DirectiveRecordResult:
        directives, problems = parse_directives(payloads, run_id=run_id)
        await self._repository.replace_all(run_id=run_id, directives=directives)
        return DirectiveRecordResult(
            accepted=sum(1 for item in directives if not item.rejected_reason),
            downgraded=sum(1 for item in directives if item.rejected_reason),
            unfilable=tuple(problems),
        )

    async def current(self) -> list[OrderDirective]:
        return await self._repository.list_current()

    async def uncovered(self, resting_order_ids: list[str]) -> list[str]:
        """Resting orders the current list says nothing about.

        Silence is the failure mode this feature exists for, so it gets counted
        rather than inferred. An order nobody addressed is not the same as one
        deliberately left alone: the second is a decision, the first is a gap.
        """

        covered = {item.order_id for item in await self.current()}
        return [order_id for order_id in resting_order_ids if order_id not in covered]

    async def note_repriced(self, order_id: str) -> None:
        await self._repository.record_reprice(order_id=order_id)


def summarize(directives: list[OrderDirective]) -> dict[str, int]:
    """Counts per action, for the trace and the panel."""

    counts = {action.value: 0 for action in DirectiveAction}
    for directive in directives:
        counts[directive.action.value] += 1
    return counts


__all__ = ["DirectiveRecordResult", "OrderDirectiveService", "summarize"]
