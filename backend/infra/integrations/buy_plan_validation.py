"""Do not replace a reviewed buy's invalidation conditions after submitting it."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.order_directives import DirectiveAction
from backend.business.order_directives.buy_conditions import check_buy_conditions
from backend.business.order_directives.parsing import parse_directives
from backend.infra.db.models import ToolInvocationModel
from backend.infra.integrations.execution_check import _mapping, _order_id
from backend.stock_api import MxMoniClient


async def validate_buy_plan(
    factory: async_sessionmaker[AsyncSession],
    portfolio: MxMoniClient | None,
    run_id: int,
    payloads: list[dict[str, Any]],
) -> None:
    plans, _ = parse_directives(payloads, run_id=run_id)
    if portfolio is not None:
        portfolio.clear_read_cache()
        orders = {o.order_id: o for o in await portfolio.get_orders()}
        for plan in plans:
            order = orders.get(plan.order_id)
            if (
                order is not None
                and order.direction == "BUY"
                and plan.action is DirectiveAction.HOLD
                and (
                    plan.cancel_if_price_below is not None
                    or plan.cancel_if_price_above is not None
                )
            ):
                if order.order_price is None:
                    raise ValueError("买单价格缺失，无法核对失效条件")
                check_buy_conditions(
                    order.order_price,
                    below=plan.cancel_if_price_below,
                    above=plan.cancel_if_price_above,
                )
    async with factory() as session:
        rows = list(
            await session.scalars(
                select(ToolInvocationModel).where(
                    ToolInvocationModel.run_id == run_id,
                    ToolInvocationModel.tool_name.in_(("trade", "prepare_buy")),
                )
            )
        )
    reviews = {
        r.tool_call_id: _mapping(r.result_json)
        for r in rows
        if r.tool_name == "prepare_buy" and r.status == "COMPLETED"
    }
    for call in rows:
        if call.tool_name != "trade":
            continue
        oid = _order_id(_mapping(call.result_json))
        review = reviews.get(
            str(_mapping(call.arguments_json).get("buy_review_call_id"))
        )
        if review is None or review.get("status") != "approved":
            continue
        for plan in plans:
            if plan.order_id != oid or plan.action is not DirectiveAction.HOLD:
                continue
            if plan.cancel_if_price_below != review.get(
                "cancel_if_price_below"
            ) or plan.cancel_if_price_above != review.get("cancel_if_price_above"):
                raise ValueError(
                    "本轮新买单的价格失效条件与买入审核不一致；请沿用审核计划或明确撤单"
                )
