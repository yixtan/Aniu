"""Run stage: research, decide, trade, and produce one Markdown report."""

from __future__ import annotations

from backend.business.runs.agent_runner import AgentRunnerPort
from backend.business.runs.execution import RunExecutionContext, RunReport
from backend.business.runs.stages.stage_helpers import (
    directive_payload,
    emit_stage_prompt_prepared,
    require_llm_runtime,
)
from backend.business.shared.serialization import serialize_context

_MEMORY_TOOL_PROTOCOL = "\n".join(
    (
        "<memory-tools>",
        (
            "形成买入、卖出、调仓、撤单或观望判断前，应主动调用 memory_read "
            "查询相关经验及可能反驳当前判断的经验。"
        ),
        "经验只是历史，必须结合当前市场、账户和工具事实全面判断。",
        "最终只输出 Markdown 报告正文。",
        (
            "如果有值得复用的经验和教训时调用 memory_write，"
            "保存简洁的投资经验教训和形成原因，"
            "不得保存运行总结、短暂行情或泛泛原则。"
        ),
        "</memory-tools>",
    )
)


class RunStage:
    async def execute(
        self,
        context: RunExecutionContext,
        agent_runner: AgentRunnerPort,
    ) -> RunReport:
        require_llm_runtime(context, stage_name="Run")
        stage_settings = context.snapshot.settings_for_stage("Run")
        market_open = (
            bool(context.market_session_is_open())
            if callable(context.market_session_is_open)
            else False
        )
        runtime_payload: dict[str, object] = {"market_session_open": market_open}
        prompt_parts = [stage_settings.prompt, _MEMORY_TOOL_PROTOCOL]
        # Both the list and the instruction about it are left out when nothing
        # is followed: there is nothing to screen, and asking a model to
        # consider an empty list only invites it to say so.
        if context.followed_companies:
            runtime_payload["watchlist"] = [
                {"symbol": symbol, "name": name}
                for symbol, name in context.followed_companies
            ]
            if stage_settings.watchlist_prompt:
                prompt_parts.append(stage_settings.watchlist_prompt)
        # What the last analysis decided about each resting order, so this one
        # revisits those decisions instead of rediscovering the account. Left
        # out entirely when there is no plan, like the watchlist: an empty list
        # only invites a sentence saying it was empty.
        if context.standing_order_plan:
            runtime_payload["standing_order_plan"] = [
                directive_payload(item, include_issuer=True)
                for item in context.standing_order_plan
            ]
            if context.previous_order_plan:
                runtime_payload["previous_order_plan"] = [
                    directive_payload(item, include_issuer=True)
                    for item in context.previous_order_plan
                ]
        # The caps already declared, newest first, and deliberately without the
        # reasoning that produced them. That reasoning used to travel with the
        # last row, and on 2026-09-22 it carried 「按id150公式1%÷5%=20%」 into a
        # run whose memory of the formula had been cleared the night before:
        # the run reported 「参数无变化」 and moved on, having re-checked
        # yesterday's arithmetic rather than deriving today's number. The
        # instruction to derive it was already in the field description and
        # lost to the data, which is the 长飞光纤 lesson — when the context
        # supplies an answer, another sentence asking for work does not.
        #
        # Numbers alone leave nothing to re-check and add what one row could
        # not say: whether this has moved lately. Left out when the table is
        # empty, like the watchlist.
        if context.recent_exposure_caps:
            runtime_payload["recent_exposure_caps"] = [
                {
                    "run_id": cap.run_id,
                    "cap_pct": cap.cap_pct,
                    "declared_at": cap.declared_at.isoformat(),
                }
                for cap in context.recent_exposure_caps
            ]
        # Unanswered objections, left out entirely when there are none — the
        # watchlist pattern. Each carries what would settle it, so answering
        # one is a check against evidence rather than a matter of opinion.
        if context.open_findings:
            runtime_payload["open_findings"] = [
                {
                    "id": item.finding_id,
                    "finding": item.finding,
                    "resolution_test": item.resolution_test,
                    "times_disputed": item.times_disputed,
                    # So a run that already asked for closure does not spend
                    # another turn re-arguing a case nobody has answered yet.
                    "settlement_proposed": item.settlement_proposed,
                }
                for item in context.open_findings
            ]
        agent_prompt = "\n\n".join(prompt_parts)
        user_prompt = "\n\n".join(
            (
                agent_prompt,
                "runtime_context:\n" + serialize_context(runtime_payload),
            )
        )
        await emit_stage_prompt_prepared(
            context,
            stage_name="Run",
            phase="run_input",
            title="任务提示词",
            summary=(
                "已发送任务规则、记忆工具协议与当前交易时段状态"
                + ("，含关注清单" if context.followed_companies else "")
                + ("，含挂单计划" if context.standing_order_plan else "")
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
            raise ValueError("run report must not be empty")
        return RunReport(
            content=content,
            tool_activity=result.tool_activity,
            transcript=result.transcript,
            total_tokens=result.total_tokens,
            cached_tokens=result.cached_tokens,
        )


__all__ = ["RunStage"]
