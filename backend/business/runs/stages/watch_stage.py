"""Watch stage: carry out the plan a run wrote for its resting orders.

The plan is handed over as data rather than described in prose, for the same
reason the watchlist is: it is at most a handful of entries, every one of them
has to be looked at, and a tool call would cost a round trip to fetch something
that is already known before the stage starts.

Nothing here decides what may be touched. That is settled in the authorizer,
which refuses a write against an order the plan does not name — a refusal the
model cannot reason past, unlike a sentence asking it to behave.
"""

from __future__ import annotations

from backend.business.order_directives import OrderDirective
from backend.business.runs.agent_runner import AgentRunnerPort
from backend.business.runs.execution import RunExecutionContext, RunReport
from backend.business.runs.stages.stage_helpers import (
    directive_payload,
    emit_stage_prompt_prepared,
    require_llm_runtime,
)
from backend.business.shared.serialization import serialize_context

_WATCH_PROTOCOL = "\n".join(
    (
        "<watch-protocol>",
        "只处理下方 order_plan 列出的委托，未列出的一律不动。",
        "逐笔说明：条件是否触发、你做了什么、依据是计划里的哪一条。",
        "没有动作也要写明没有动作，沉默视为未完成。",
        "计划提到但已不在挂单中的委托，要单独指出，不要当作已完成。",
        "</watch-protocol>",
    )
)

_NOTHING_TO_DO = "\n".join(
    (
        "<watch-protocol>",
        "本次没有挂单处置计划，因此不得进行任何撤单或下单操作。",
        "只需查询当前委托并说明现状，指出有哪些委托无人认领。",
        "</watch-protocol>",
    )
)


class WatchStage:
    async def execute(
        self,
        context: RunExecutionContext,
        agent_runner: AgentRunnerPort,
        *,
        directives: tuple[OrderDirective, ...] = (),
    ) -> RunReport:
        require_llm_runtime(context, stage_name="Watch")
        stage_settings = context.snapshot.settings_for_stage("Watch")
        market_open = (
            bool(context.market_session_is_open())
            if callable(context.market_session_is_open)
            else False
        )
        runtime_payload: dict[str, object] = {"market_session_open": market_open}
        protocol = _WATCH_PROTOCOL if directives else _NOTHING_TO_DO
        if directives:
            runtime_payload["order_plan"] = [
                directive_payload(item) for item in directives
            ]
        agent_prompt = "\n\n".join((stage_settings.prompt, protocol))
        user_prompt = "\n\n".join(
            (
                agent_prompt,
                "runtime_context:\n" + serialize_context(runtime_payload),
            )
        )
        await emit_stage_prompt_prepared(
            context,
            stage_name="Watch",
            phase="watch_input",
            title="盯盘提示词",
            summary=(
                f"已发送 {len(directives)} 笔委托的处置计划"
                if directives
                else "本次没有挂单处置计划，只能查看"
            ),
            display_prompt=agent_prompt,
            payload=runtime_payload,
            user_message=user_prompt,
        )
        result = await agent_runner.prompt(
            user_prompt,
            abort_signal=context.abort_signal,
        )
        content = result.content.strip()
        if not content:
            raise ValueError("watch record must not be empty")
        return RunReport(
            content=content,
            tool_activity=result.tool_activity,
            transcript=result.transcript,
            total_tokens=result.total_tokens,
            cached_tokens=result.cached_tokens,
        )


__all__ = ["WatchStage"]
