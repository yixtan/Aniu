"""Recover trade outcomes from durable invocations and observed account orders."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.business.runs.reconciliation import ExecutionCheck, ExecutionResultRow
from backend.business.shared import RunNotFoundError
from backend.infra.db.models import (
    AccountOrderCacheModel,
    StrategyRunModel,
    ToolInvocationModel,
)
from backend.infra.repositories.account_cache_repo import AccountCacheRepository


def _mapping(raw: str | None) -> dict[str, object]:
    try:
        value = json.loads(raw or "null")
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _order_id(result: dict[str, object]) -> str | None:
    for source in (result, result.get("data")):
        if isinstance(source, dict):
            for key in ("orderId", "orderID", "order_id"):
                value = source.get(key)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    return str(value).strip() or None
    return None


class ExecutionCheckQuery:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def check(self, run_id: int) -> ExecutionCheck:
        run = await self.session.get(StrategyRunModel, run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        now = datetime.now(UTC)
        state = await AccountCacheRepository(self.session).get_state()
        observed = state.captured_at if state else None
        stale = (
            observed is None
            or (now - observed).total_seconds() > 300
            or bool(state and state.last_refresh_error)
        )
        invocations = list(
            await self.session.scalars(
                select(ToolInvocationModel)
                .where(
                    ToolInvocationModel.run_id == run_id,
                    ToolInvocationModel.tool_name.in_(("trade", "cancel")),
                )
                .order_by(
                    ToolInvocationModel.created_at, ToolInvocationModel.tool_call_id
                )
            )
        )
        # Read every cached order, not the dashboard's twenty-row page.
        orders = {
            o.order_id: o
            for o in await self.session.scalars(select(AccountOrderCacheModel))
        }
        items = []
        for call in invocations:
            result = _mapping(call.result_json)
            arguments = _mapping(call.arguments_json)
            instruction = str(arguments.get("instruction") or "未记录指令")
            oid = _order_id(result) if call.tool_name == "trade" else None
            if call.tool_name == "cancel":
                parts = instruction.split()
                if len(parts) == 3 and parts[0] == "撤单":
                    oid = parts[2]
            order = orders.get(oid) if oid else None
            # A cache predating this call is not evidence for its result.
            if observed is None or observed < datetime.fromisoformat(call.created_at):
                order = None
            outcome, explanation = "unknown", "结果待确认；不得据此重复提交交易。"
            if call.tool_name == "trade" and oid and call.status == "COMPLETED":
                outcome, explanation = "accepted", "已收到委托编号；受理不代表成交。"
            if order is not None:
                if call.tool_name == "cancel":
                    if order.status in {"CANCELLED", "PARTIAL_CANCELLED"}:
                        outcome, explanation = "cancelled", "账户记录已确认撤单。"
                    else:
                        explanation = "撤单结果尚未确认，请核对委托状态和已成交数量。"
                elif (
                    order.status == "FILLED" and order.filled_quantity == order.quantity
                ):
                    outcome, explanation = "filled", "账户委托记录确认全部成交。"
                elif order.filled_quantity > 0:
                    outcome, explanation = (
                        "partial",
                        "账户确认部分成交；剩余部分以委托状态为准。",
                    )
                elif order.status in {"CANCELLED", "PARTIAL_CANCELLED", "REJECTED"}:
                    outcome, explanation = (
                        "closed",
                        "委托已终结且未成交；REJECTED也可能表示收盘作废。",
                    )
                else:
                    outcome, explanation = "accepted", "账户中可见委托，尚无成交证据。"
            elif call.status == "FAILED":
                explanation = "调用报错，但不能仅凭报错断言上游未受理；请先核对账户。"
            if stale and order is not None:
                explanation += " 这是上次观测结果，当前数据待刷新。"
            items.append(
                ExecutionResultRow(
                    tool_call_id=call.tool_call_id,
                    operation=call.tool_name,
                    instruction=instruction,
                    outcome=outcome,
                    order_id=oid,
                    order_status=order.status if order else None,
                    filled_quantity=order.filled_quantity if order else None,
                    observed_at=observed if order else None,
                    explanation=explanation,
                )
            )
        interrupted = run.status in {"FAILED", "ABORTED"}
        unfinished = []
        trace = run.trace_json if isinstance(run.trace_json, dict) else {}
        stages = trace.get("stages", [])
        for stage in stages if isinstance(stages, list) else []:
            if isinstance(stage, dict) and stage.get("status") in {
                "failed",
                "running",
                "pending",
                "aborted",
            }:
                unfinished.append(
                    str(stage.get("title") or stage.get("key") or "未完成阶段")
                )
        if interrupted and not run.summary:
            unfinished.append("完整运行报告未生成")
        return ExecutionCheck(
            run_id=run_id,
            run_status=run.status,
            checked_at=now,
            account_observed_at=observed,
            evidence_stale=stale,
            interrupted=interrupted,
            unfinished_steps=list(dict.fromkeys(unfinished)),
            items=items,
            notice="系统按交易调用记录和账户委托核对，不重新下单或重跑研究。无委托编号的请求不按股票、价格猜测归属。"
            + (
                " 未发现持久化交易调用记录，不代表旧版本或中断窗口内一定没有交易。"
                if not items
                else ""
            ),
        )
