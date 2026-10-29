"""Claim a cancellation once, commit before HTTP, never replay uncertainty."""

from dataclasses import asdict

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from backend.business.account import PortfolioOrderSnapshot
from backend.business.order_directives.valuation_cancellation import (
    TERMINAL_CANCELLATIONS,
)
from backend.business.runs.trading_facts import OrderValuationReview
from backend.infra.db.models import ValuationCancellationModel, utc_now_iso
from backend.infra.repositories.tool_invocation_repo import ToolInvocationRepository


class ValuationCancellationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def outstanding(self) -> list[ValuationCancellationModel]:
        return list(
            await self.session.scalars(
                select(ValuationCancellationModel).where(
                    ValuationCancellationModel.state.not_in(TERMINAL_CANCELLATIONS)
                )
            )
        )

    async def ensure(
        self,
        order: PortfolioOrderSnapshot,
        review: OrderValuationReview,
        cause: str,
        run_id: int,
    ) -> ValuationCancellationModel:
        await self.session.execute(
            insert(ValuationCancellationModel)
            .values(
                order_id=order.order_id,
                symbol=order.symbol,
                stock_name=order.stock_name,
                order_price=order.order_price,
                quantity=order.quantity,
                submitted_at=order.submitted_at.isoformat()
                if order.submitted_at
                else None,
                state="REQUIRED",
                cause=cause,
                explanation=review.explanation,
                evidence=asdict(review),
                triggered_by_run_id=run_id,
                attempts=0,
                filled_quantity=order.filled_quantity,
            )
            .on_conflict_do_nothing(index_elements=["order_id"])
        )
        row = await self.session.get(ValuationCancellationModel, order.order_id)
        assert row is not None
        if row.state in TERMINAL_CANCELLATIONS:
            row.state = "UNKNOWN"
            row.explanation = (
                "账户重新出现已终结的委托编号，状态矛盾或编号复用；待核对，不自动重发。"
            )
        return row

    async def claim(
        self, order_id: str, run_id: int
    ) -> ValuationCancellationModel | None:
        row = await self.session.scalar(
            update(ValuationCancellationModel)
            .where(
                ValuationCancellationModel.order_id == order_id,
                ValuationCancellationModel.state == "REQUIRED",
            )
            .values(
                state="SUBMITTING",
                request_run_id=run_id,
                attempts=ValuationCancellationModel.attempts + 1,
                updated_at=utc_now_iso(),
            )
            .returning(ValuationCancellationModel)
        )
        if row is None:
            return None
        call_id = f"system-valuation-cancel:{order_id}:{row.attempts}"
        row.request_call_id = call_id
        await ToolInvocationRepository(self.session).reserve(
            run_id=run_id,
            tool_call_id=call_id,
            tool_name="cancel",
            arguments={
                "instruction": f"撤单 {order_id} {row.symbol}",
                "source": "valuation_policy",
            },
        )
        return row


async def refuse_managed_cancellation(
    session: AsyncSession,
    *,
    symbol: str | None = None,
    order_id: str | None = None,
    run_id: int | None = None,
) -> None:
    condition: ColumnElement[bool] = ValuationCancellationModel.state.not_in(
        TERMINAL_CANCELLATIONS
    )
    if run_id is not None:
        condition = or_(
            condition,
            ValuationCancellationModel.triggered_by_run_id == run_id,
            ValuationCancellationModel.request_run_id == run_id,
        )
    statement = select(ValuationCancellationModel.order_id).where(condition)
    if symbol is not None:
        statement = statement.where(ValuationCancellationModel.symbol == symbol)
    if order_id is not None:
        statement = statement.where(ValuationCancellationModel.order_id == order_id)
    if await session.scalar(statement.limit(1)) is not None:
        raise ValueError(
            "存在系统估值撤单待确认；禁止模型重复撤单、改价重挂或新增同标的买单"
        )
