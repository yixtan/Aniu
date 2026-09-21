"""Run-stage tool: say what you may hold today, before you trade.

The cap used to live in long-term memory, where the agent owned it and could
restate it. Six memories carried 「总敞口约20%封顶」 inside six days, every new
rule inheriting it, and when a finding finally asked where the number came
from the answer was a formula — 容忍回撤 1% ÷ 单腿极端亏损 5% — whose only
market-facing trigger fed terms that contain no market state. It recomputed to
20% by construction.

So the number is no longer a rule. It is a decision this run makes, in front
of the record, with what it cost written down beside it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.exposure import ExposureCap, ExposureCapService
from backend.infra.integrations.tool_policy import SideEffectLevel
from backend.infra.repositories.exposure_cap_repo import ExposureCapRepository
from backend.llm import AbortSignal, ProviderJsonObject, ToolDefinition

_RUN_STAGES = ("Run",)


@dataclass
class DeclareExposureCapTool:
    """Records the cap. Refuses nothing.

    Deliberately not an enforcement point: an order goes out through the
    trading tools and this never sees it. What it buys is that the number is
    stated before the trading rather than narrated after it, kept as a field
    rather than as a line in a report, and put in front of the independent
    review, which can then ask why today's differs from Friday's — or why it
    does not.
    """

    session_factory: async_sessionmaker[AsyncSession]
    name: str = "declare_exposure_cap"
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
                "声明本次运行的当日总敞口上限，**在任何下单之前调用**。"
                "这个数字由你按当下行情与账户自身记录决定，"
                "不是别处规定的常量，也不要写进长期记忆——"
                "长期记忆存的是「什么条件下该定多少」的方法，不是今天这个数。"
                "没有声明就没有当日口径，报告里也无从核对。"
            ),
            "parameters": cast(
                ProviderJsonObject,
                {
                    "type": "object",
                    "properties": {
                        "cap_pct": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                            "maximum": 100,
                            "description": "今日总敞口上限，占账户总资产的百分比。",
                        },
                        "basis": {
                            "type": "string",
                            "description": (
                                "凭什么定成这个数：今天的行情结构、"
                                "持仓之间的相关性、近期已了结样本的实际亏损分布。"
                                "只写今天成立的依据，不要复述以往的结论。"
                            ),
                        },
                        "changed_from": {
                            "type": "string",
                            "description": (
                                "上一次是多少，这次为什么动、或者为什么不动。"
                                "不动也要给出今天的理由——"
                                "「沿用未变」不是理由。"
                            ),
                        },
                        "forgone": {
                            "type": "string",
                            "description": (
                                "今天因为这个上限放弃了什么："
                                "哪些标的符合你的买入标准却没买、它们当时什么价位。"
                                "这是判断上限定得贵不贵的唯一证据。"
                                "如果今天没有因它放弃任何东西，写明「未触及上限」。"
                            ),
                        },
                    },
                    "required": ["cap_pct", "basis", "changed_from", "forgone"],
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
        **kwargs: Any,
    ) -> object:
        del tool_call_id
        if abort_signal is not None and abort_signal.aborted:
            raise RuntimeError("declare_exposure_cap aborted")
        cap_pct = kwargs.get("cap_pct")
        if not isinstance(cap_pct, int | float) or isinstance(cap_pct, bool):
            raise ValueError("declare_exposure_cap requires a numeric cap_pct")
        try:
            cap = ExposureCap(
                run_id=run_id,
                cap_pct=float(cap_pct),
                basis=str(kwargs.get("basis") or ""),
                changed_from=str(kwargs.get("changed_from") or ""),
                forgone=str(kwargs.get("forgone") or ""),
            )
        except ValueError as exc:
            # The reasons are the instrument. A cap with none is the number on
            # its own, which is what this replaces.
            raise ValueError(str(exc)) from exc
        async with self.session_factory() as session:
            stored = await ExposureCapService(
                ExposureCapRepository(session)
            ).declare(cap)
            await session.commit()
        return {
            "status": "ok",
            "cap_pct": stored.cap_pct,
            "run_id": run_id,
        }

    async def run(self, **_: object) -> object:
        raise RuntimeError("declare_exposure_cap requires trusted run call context")


__all__ = ["DeclareExposureCapTool"]
