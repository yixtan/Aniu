"""Persistence adapter for the order directives currently in force."""

from __future__ import annotations

from datetime import UTC, datetime, time

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.business.order_directives import (
    DirectiveAction,
    OrderDirective,
    RepricePlan,
)
from backend.infra.db.models import OrderDirectiveModel


class OrderDirectiveRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_all(
        self, *, run_id: int, directives: list[OrderDirective]
    ) -> None:
        """Clear the table and write this run's list in one transaction.

        Delete-then-insert rather than upsert-and-prune: an order the new run
        did not mention has no directive, and leaving the previous run's entry
        behind would quietly let a superseded decision keep its authority —
        which is the exact failure this feature was built to stop.
        """

        await self._session.execute(delete(OrderDirectiveModel))
        for directive in directives:
            self._session.add(_to_model(directive, run_id=run_id))
        await self._session.flush()

    async def list_current(self) -> list[OrderDirective]:
        models = await self._session.scalars(
            select(OrderDirectiveModel).order_by(OrderDirectiveModel.id)
        )
        return [_to_domain(model) for model in models]

    async def record_reprice(self, *, order_id: str) -> None:
        await self._session.execute(
            update(OrderDirectiveModel)
            .where(OrderDirectiveModel.order_id == order_id)
            .values(repriced_times=OrderDirectiveModel.repriced_times + 1)
        )
        await self._session.flush()


def _to_model(directive: OrderDirective, *, run_id: int) -> OrderDirectiveModel:
    plan = directive.reprice
    return OrderDirectiveModel(
        order_id=directive.order_id,
        symbol=directive.symbol,
        stock_name=directive.stock_name,
        action=directive.action.value,
        note=directive.note,
        issued_by_run_id=run_id,
        cancel_if_price_above=directive.cancel_if_price_above,
        cancel_if_price_below=directive.cancel_if_price_below,
        cancel_if_unfilled_after=(
            None
            if directive.cancel_if_unfilled_after is None
            else directive.cancel_if_unfilled_after.strftime("%H:%M")
        ),
        reprice_new_price=None if plan is None else plan.new_price,
        reprice_max_times=0 if plan is None else plan.max_times,
        reprice_when_price_above=None if plan is None else plan.when_price_above,
        reprice_when_price_below=None if plan is None else plan.when_price_below,
        repriced_times=directive.repriced_times,
        rejected_reason=directive.rejected_reason,
        issued_at=datetime.now(tz=UTC).isoformat(),
    )


def _clock(value: str | None) -> time | None:
    if not value:
        return None
    hour, minute = (int(part) for part in value.split(":", 1))
    return time(hour=hour, minute=minute)


def _to_domain(model: OrderDirectiveModel) -> OrderDirective:
    plan = (
        None
        if model.reprice_new_price is None
        else RepricePlan(
            new_price=model.reprice_new_price,
            max_times=max(1, model.reprice_max_times),
            when_price_above=model.reprice_when_price_above,
            when_price_below=model.reprice_when_price_below,
        )
    )
    action = DirectiveAction(model.action)
    return OrderDirective(
        order_id=model.order_id,
        symbol=model.symbol,
        stock_name=model.stock_name,
        # A reprice row whose plan did not survive the round trip would fail the
        # model's own invariant, so it comes back as the inert reading instead.
        action=DirectiveAction.HOLD
        if plan is None and action is DirectiveAction.REPRICE
        else action,
        note=model.note,
        issued_by_run_id=model.issued_by_run_id,
        cancel_if_price_above=model.cancel_if_price_above,
        cancel_if_price_below=model.cancel_if_price_below,
        cancel_if_unfilled_after=_clock(model.cancel_if_unfilled_after),
        reprice=plan,
        repriced_times=model.repriced_times,
        rejected_reason=model.rejected_reason,
        issued_at=datetime.fromisoformat(model.issued_at),
    )


__all__ = ["OrderDirectiveRepository"]
