"""End-of-analysis order handoff checks independent of model claims."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from backend.business.account import PortfolioOrderSnapshot
from backend.business.order_directives.models import DirectiveAction, OrderDirective


@dataclass(frozen=True)
class HandoffCheck:
    status: str
    checked_at: str
    active_count: int
    covered_count: int
    issues: tuple[str, ...]

    @property
    def summary(self) -> str:
        if self.status == "complete":
            return f"交接完成：{self.active_count}笔有效挂单均已核对。"
        if self.status == "incomplete":
            return (
                f"交接未完成：{self.active_count}笔有效挂单中"
                f"仅{self.covered_count}笔有本轮有效计划。"
            )
        return "交接待核对：无法确认全部有效挂单及其处置计划。"

    def payload(self) -> dict[str, object]:
        return dict(
            status=self.status,
            checked_at=self.checked_at,
            active_count=self.active_count,
            covered_count=self.covered_count,
            issues=list(self.issues),
            summary=self.summary,
        )

    def markdown(self) -> str:
        return (
            "\n\n## 挂单交接检查（系统核对）\n\n"
            + self.summary
            + "\n"
            + "\n".join("- " + issue for issue in self.issues)
        )


def check_handoff(
    run_id: int,
    orders: list[PortfolioOrderSnapshot],
    directives: tuple[OrderDirective, ...],
) -> HandoffCheck:
    issues: list[str] = []
    active = covered = 0
    unknown = False
    counts = Counter(d.order_id for d in directives)
    by_id = {d.order_id: d for d in directives}
    order_counts = Counter(o.order_id for o in orders)
    for order in orders:
        if order.status == "FILLED" and order.filled_quantity != order.quantity:
            unknown = True
            issues.append(f"委托{order.order_id}成交状态与数量不一致，待核对。")
            continue
        if order.status in {"FILLED", "CANCELLED", "PARTIAL_CANCELLED", "REJECTED"}:
            continue
        if order.quantity <= 0 or not 0 <= order.filled_quantity <= order.quantity:
            unknown = True
            issues.append(f"委托{order.order_id}数量异常，待核对。")
            continue
        if order.quantity == order.filled_quantity:
            continue
        active += 1
        if (
            order_counts[order.order_id] != 1
            or order.status
            not in {"PENDING", "PARTIAL", "PENDING_CANCEL", "PARTIAL_PENDING_CANCEL"}
            or order.direction not in {"BUY", "SELL"}
        ):
            unknown = True
            issues.append(f"委托{order.order_id}状态不明确，待核对。")
            continue
        plan = by_id.get(order.order_id)
        reason = ""
        if plan is None or plan.issued_by_run_id != run_id:
            reason = "缺少本轮处置计划"
        elif counts[order.order_id] != 1:
            reason = "计划重复"
        elif plan.symbol != order.symbol:
            reason = "计划股票与委托不一致"
        elif plan.rejected_reason:
            reason = "计划已降级，需重新声明"
        elif plan.action is DirectiveAction.REPRICE:
            reason = "盯盘不支持改价，需明确可执行处置"
        elif any(
            v is not None and (not math.isfinite(v) or v <= 0)
            for v in (plan.cancel_if_price_above, plan.cancel_if_price_below)
        ):
            reason = "价格条件无效"
        if reason:
            issues.append(
                f"{order.stock_name}（{order.symbol}）委托{order.order_id}：{reason}。"
            )
        else:
            covered += 1
    status = "unknown" if unknown else "incomplete" if issues else "complete"
    return HandoffCheck(
        status, datetime.now(UTC).isoformat(), active, covered, tuple(issues)
    )
