"""Run-stage Agent tool for handing resting orders forward as written intent."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.order_directives import (
    DirectiveAction,
    OrderDirectiveService,
)
from backend.infra.integrations.tool_policy import SideEffectLevel
from backend.infra.repositories.order_directive_repo import OrderDirectiveRepository
from backend.llm import AbortSignal, ProviderJsonObject, ToolDefinition

_RUN_STAGES = ("Run",)
logger = logging.getLogger(__name__)


def _price(description: str) -> dict[str, object]:
    return {"type": "number", "exclusiveMinimum": 0, "description": description}


def _directive_schema() -> ProviderJsonObject:
    return cast(
        ProviderJsonObject,
        {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "query_portfolio 返回的委托 orderId。",
                },
                "symbol": {"type": "string", "description": "股票代码。"},
                "stock_name": {"type": "string", "description": "股票名称。"},
                "action": {
                    "type": "string",
                    "enum": [action.value for action in DirectiveAction],
                    "description": (
                        "cancel=下一轮立即撤；hold=留着，仅在下列条件命中时撤；"
                        "reprice=条件命中时撤旧下新，必须同时给出 reprice。"
                    ),
                },
                "note": {
                    "type": "string",
                    "description": "这样处置的理由，一句话即可。",
                },
                "cancel_if_price_above": _price("现价高于此值就撤（多用于买单）。"),
                "cancel_if_price_below": _price("现价低于此值就撤（多用于卖单）。"),
                "cancel_if_unfilled_after": {
                    "type": "string",
                    "pattern": "^[0-9]{2}:[0-9]{2}$",
                    "description": "到这个时刻仍未成交就撤，如 14:30。",
                },
                "reprice": {
                    "type": "object",
                    "properties": {
                        "when_price_above": _price("现价高于此值时改价。"),
                        "when_price_below": _price("现价低于此值时改价。"),
                        "new_price": _price(
                            "改成这个价，必须是确定的数字，不能是算式。"
                        ),
                        "max_times": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 3,
                            "description": "本轮最多改几次。",
                        },
                    },
                    "required": ["new_price"],
                    "additionalProperties": False,
                },
            },
            "required": ["order_id", "action", "note"],
            "additionalProperties": False,
        },
    )


@dataclass(slots=True)
class DeclareOrderPlanTool:
    """Record, for every resting order, what should happen to it and when.

    The conditions are deliberately narrow — price, clock, fill — because the
    task that acts on them is given no research tools. A condition it cannot
    settle mechanically is a condition nobody settles, and the order would keep
    its authority by default, which is the failure this exists to prevent.
    """

    session_factory: async_sessionmaker[AsyncSession]
    name: str = "declare_order_plan"
    enabled_stages: tuple[str, ...] = field(default=_RUN_STAGES)
    side_effect_level: SideEffectLevel = SideEffectLevel.WRITE
    execution_mode: str = "sequential"
    requires_market_open: bool = False

    def is_write_call(self, arguments: object) -> bool:
        del arguments
        return True

    def to_tool_definition(self) -> ToolDefinition:
        return {
            "name": self.name,
            "description": (
                "登记本次运行对每一笔未成交委托的处置，供高频盯盘任务执行。"
                "**整份替换**：调用一次并带上全部未成交委托，"
                "没有写进来的委托将不受任何处置约束。未调用则盯盘不会动手。"
            ),
            "parameters": cast(
                ProviderJsonObject,
                {
                    "type": "object",
                    "properties": {
                        "directives": {
                            "type": "array",
                            "items": _directive_schema(),
                            "description": "每一笔未成交委托一条，逐笔表态。",
                        }
                    },
                    "required": ["directives"],
                    "additionalProperties": False,
                },
            ),
        }

    async def run_for_call(
        self,
        *,
        run_id: int,
        tool_call_id: str,
        abort_signal: AbortSignal | None = None,
        directives: list[dict[str, Any]] | None = None,
    ) -> object:
        del tool_call_id
        if abort_signal is not None and abort_signal.aborted:
            raise RuntimeError("declare_order_plan aborted")
        if directives is None:
            raise ValueError("declare_order_plan requires a directives list")
        async with self.session_factory() as session:
            result = await OrderDirectiveService(
                OrderDirectiveRepository(session)
            ).record(directives, run_id=run_id)
            await session.commit()
        if result.downgraded or result.unfilable:
            logger.warning(
                "order plan partially unusable: run_id=%s downgraded=%s unfilable=%s",
                run_id,
                result.downgraded,
                len(result.unfilable),
            )
        return {"status": "ok", **result.as_payload}

    async def run(self, **_: object) -> object:
        raise RuntimeError("declare_order_plan requires trusted run call context")


__all__ = ["DeclareOrderPlanTool"]
