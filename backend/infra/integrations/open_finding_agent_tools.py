"""Run-stage Agent tool for answering an open finding on the record.

The disposition has to leave the report and land on the finding. Stated only
in prose it cannot be counted, and the count is the whole mechanism: an
objection that is answered and never acted on has to be as visible as one that
is addressed, or this decays into agreement and nobody notices.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.open_findings import OpenFindingService, Verdict
from backend.infra.integrations.tool_policy import SideEffectLevel
from backend.infra.repositories.open_finding_repo import OpenFindingRepository
from backend.llm import AbortSignal, ProviderJsonObject, ToolDefinition

_RUN_STAGES = ("Run",)
logger = logging.getLogger(__name__)


@dataclass
class DisposeOpenFindingTool:
    """Say where this run stands on one open finding.

    It cannot close one. Given that, it would write 「经复核，该顾虑不成立」 on
    the third pass and move on — which is exactly what it already does in the
    memory it writes for itself. Closing stays with the operator.
    """

    session_factory: async_sessionmaker[AsyncSession]
    name: str = "dispose_open_finding"
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
                "对 runtime_context.open_findings 里的一条议题表态。"
                "每条议题调用一次，一次一条。"
                "你无权关闭议题，关闭权在操作者；"
                "表态会被记录，反复 disagreed 而不调整是可见的。"
            ),
            "parameters": cast(
                ProviderJsonObject,
                {
                    "type": "object",
                    "properties": {
                        "finding_id": {
                            "type": "integer",
                            "description": "议题的 id，取自 runtime_context。",
                        },
                        "verdict": {
                            "type": "string",
                            "enum": [
                                Verdict.ADJUSTED.value,
                                Verdict.SETTLED.value,
                                Verdict.DISAGREED.value,
                                Verdict.UNDECIDED.value,
                            ],
                            "description": (
                                "ADJUSTED=已按此调整（说的是你自己的计划）；"
                                "SETTLED=resolution_test 已被满足（说的是计划之外"
                                "已经发生的事，note 必须指出那个证据是什么）；"
                                "DISAGREED=不同意；UNDECIDED=尚无法判断。"
                                "SETTLED 不会关闭议题，它只是请操作者来确认；"
                                "若该议题已标着 settlement_proposed，说明上一次"
                                "已经提过，仍在等人，不必重复论证。"
                            ),
                        },
                        "note": {
                            "type": "string",
                            "description": (
                                "对着该议题的 resolution_test 说话："
                                "调整了什么／为什么不同意且什么证据会改变看法／"
                                "缺哪个数据要怎么取。"
                            ),
                        },
                    },
                    "required": ["finding_id", "verdict", "note"],
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
            raise RuntimeError("dispose_open_finding aborted")
        finding_id = kwargs.get("finding_id")
        verdict_raw = kwargs.get("verdict")
        note = kwargs.get("note")
        if not isinstance(finding_id, int):
            raise ValueError("dispose_open_finding requires finding_id")
        if not isinstance(note, str) or not note.strip():
            # A verdict with no reasoning cannot be checked against the
            # finding's own test, which is the only thing that settles it.
            raise ValueError("dispose_open_finding requires a note")
        try:
            verdict = Verdict(str(verdict_raw))
        except ValueError as exc:
            raise ValueError(
                "verdict must be ADJUSTED, SETTLED, DISAGREED or UNDECIDED"
            ) from exc
        async with self.session_factory() as session:
            stored = await OpenFindingService(
                OpenFindingRepository(session)
            ).dispose(finding_id, run_id=run_id, verdict=verdict, note=note)
            await session.commit()
        if stored is None:
            raise ValueError(f"no such open finding: {finding_id}")
        return {
            "status": "ok",
            "finding_id": finding_id,
            "verdict": verdict.value,
            "times_disputed": stored.times_disputed,
            # Echoed so a run that just proposed settlement can see it landed
            # as a request rather than as a close.
            "settlement_proposed": stored.settlement_proposed,
        }

    async def run(self, **_: object) -> object:
        raise RuntimeError("dispose_open_finding requires trusted run call context")


__all__ = ["DisposeOpenFindingTool"]
