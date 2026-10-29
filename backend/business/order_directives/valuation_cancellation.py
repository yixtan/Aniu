"""Authoritative lifecycle of a system-requested valuation cancellation."""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


def safe_cell(text: str) -> str:
    return (
        text.replace("|", "／").replace("\n", " ").replace("<", "＜").replace(">", "＞")
    )


def local_time(value: str | None) -> str:
    if value is None:
        return "未确认"
    try:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            return "未确认"
        return moment.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        return "未确认"


TERMINAL_CANCELLATIONS = frozenset({"CANCELLED", "PARTIAL_CANCELLED", "CLOSED"})


@dataclass(frozen=True)
class CancellationObservation:
    order_id: str
    symbol: str
    stock_name: str
    state: str
    cause: str
    explanation: str
    filled_quantity: int
    observed_at: str | None
    valuation_explanation: str = ""
    valuation_observed_at: str | None = None


@dataclass(frozen=True)
class ValuationCancellationReport:
    items: tuple[CancellationObservation, ...] = ()
    issues: tuple[str, ...] = ()
    tool_activity: tuple[dict[str, object], ...] = ()

    @property
    def managed_order_ids(self) -> frozenset[str]:
        return frozenset(item.order_id for item in self.items)

    def markdown(self) -> str:
        lines = ["\n\n## 挂单估值自动处置（系统生成）", ""]
        if not self.items:
            lines.append("本轮没有新增估值撤单请求；账户或估值查询异常见下方提示。")
        labels = {
            "REQUIRED": "待提交（等待可撤时段或订单确认）",
            "SUBMITTING": "请求结果待确认，不自动重发",
            "SUBMITTED": "撤单请求已受理，尚未确认撤销",
            "UNKNOWN": "请求或订单状态待确认，不自动重发",
            "PENDING_CANCEL": "账户显示撤单处理中",
            "CANCELLED": "账户已确认撤销",
            "PARTIAL_CANCELLED": "账户已确认余量撤销，已成交部分保留",
            "CLOSED": "账户已确认委托终结，无需再撤",
        }
        for item in self.items:
            cause = (
                "估值未通过"
                if item.cause == "VALUATION_FAILED"
                else "无法核验，保护性撤单"
            )
            lines.append(
                f"- {safe_cell(item.stock_name)}（{safe_cell(item.symbol)}）"
                f"委托{safe_cell(item.order_id)}：{cause}；"
                f"{labels.get(item.state, '状态待确认')}；"
                f"已成交{item.filled_quantity}股。{safe_cell(item.explanation)}"
            )
            lines.append(
                f"  估值依据：{safe_cell(item.valuation_explanation)}；"
                f"估值行情时间：{local_time(item.valuation_observed_at)}；"
                f"账户最后核对（北京时间）：{local_time(item.observed_at)}。"
            )
        lines.extend("- " + safe_cell(issue) for issue in self.issues)
        lines.append("")
        lines.append("撤单确认前不释放敞口；系统不卖出已成交持仓，不改价重挂。")
        return "\n".join(lines)
