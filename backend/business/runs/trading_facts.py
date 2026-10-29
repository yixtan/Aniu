"""Observed trading facts rendered by the system, outside the model's text."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


def local_time(value: str | None) -> str:
    try:
        moment = datetime.fromisoformat(value or "")
        if moment.tzinfo is None:
            return "时间未确认"
        return moment.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        return "时间未确认"


def safe_cell(text: str) -> str:
    return (
        text.replace("|", "／").replace("\n", " ").replace("<", "＜").replace(">", "＞")
    )


@dataclass(frozen=True)
class OrderValuationReview:
    order_id: str
    symbol: str
    stock_name: str
    order_price: float | None
    remaining_quantity: int
    pretrade_status: str
    valuation_status: str
    observed_at: str | None
    stale: bool
    explanation: str
    checks: tuple[dict[str, object], ...] = ()
    checked_at: str | None = None
    reference_price: float | None = None
    reference_pe_ttm: float | None = None
    reference_pb: float | None = None

    @property
    def approval_label(self) -> str:
        return {
            "recorded": "有下单前审核记录",
            "not_recorded": "无下单前审核记录",
            "unknown": "下单前审核待核对",
        }.get(self.pretrade_status, "下单前审核待核对")

    @property
    def valuation_label(self) -> str:
        label = {"passed": "估值通过", "failed": "估值未通过"}.get(
            self.valuation_status, "估值待核对"
        )
        return label + ("（旧报价快照）" if self.stale else "")


@dataclass(frozen=True)
class TradingFacts:
    checked_at: str
    account_observed_at: str | None = None
    total_asset: float | None = None
    market_value: float | None = None
    pending_buy_amount: float | None = None
    frozen_cash: float | None = None
    reserved_buy_amount: float | None = None
    potential_amount: float | None = None
    potential_pct: float | None = None
    cap_pct: float | None = None
    remaining_amount: float | None = None
    orders: tuple[OrderValuationReview, ...] = ()
    issues: tuple[str, ...] = ()

    def payload(self) -> dict[str, object]:
        return asdict(self)

    def markdown(self) -> str:
        lines = [
            "\n\n## 审核状态与潜在敞口（系统生成）",
            "",
            f"核对时间：{local_time(self.checked_at)}（北京时间）。",
            "本节由账户与工具账本生成，审核状态和敞口以本节为准；模型分析中的相关表述不构成系统确认。",
        ]
        if self.potential_amount is None:
            lines.append("潜在敞口：待核对，缺失证据不按零计算。")
        else:
            assert self.potential_pct is not None
            lines += [
                f"账户观测时间：{local_time(self.account_observed_at)}（北京时间）。",
                f"持仓市值 {self.market_value:,.2f} 元；"
                f"未成交买单剩余金额 {self.pending_buy_amount:,.2f} 元；"
                f"冻结资金 {self.frozen_cash:,.2f} 元。",
                "买入占用取未成交买单与冻结资金的较大值："
                f"{self.reserved_buy_amount:,.2f} 元。",
                f"**潜在敞口：{self.potential_amount:,.2f} 元，"
                f"占总资产 {self.potential_pct:.2f}%。**",
                "该口径已经包含全部在挂买单；全部成交后不再重复加一次。成交价及市值变化仍可能改变实际比例。",
            ]
            if self.cap_pct is not None and self.remaining_amount is not None:
                lines.append(
                    f"当日有效上限 {self.cap_pct:g}%；"
                    f"剩余额度 {self.remaining_amount:,.2f} 元（负数表示已超限）。"
                )
        if self.orders:
            lines += [
                "",
                "| 挂单 | 下单前审核 | 本次只读估值复核 | 报价观测时间 | 说明 |",
                "|---|---|---|---|---|",
            ]
            for order in self.orders:
                lines.append(
                    f"| {safe_cell(order.stock_name)}（{safe_cell(order.symbol)}）"
                    f"／{safe_cell(order.order_id)}"
                    f" | {order.approval_label} | {order.valuation_label}"
                    f" | {local_time(order.observed_at)}"
                    f" | {safe_cell(order.explanation)} |"
                )
        else:
            lines.append("本次没有可复核的有效买单；这不代表其他标的已通过审核。")
        lines += [
            "",
            "只读复核不补造下单前审核记录，不产生买入授权，不自动撤单、补单或改价。历史阈值仅有检索时间，上游未提供统计截止日。",
        ]
        lines.extend(f"- {safe_cell(issue)}" for issue in self.issues)
        return "\n".join(lines)
