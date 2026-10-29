"""Independent, read-only account and approval evidence for a Run report."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.account import PortfolioOrderSnapshot
from backend.business.account.ports import PortfolioQueryPort
from backend.business.exposure.calculation import exposure_amounts
from backend.business.runs.trading_facts import TradingFacts
from backend.business.shared.trading.value_objects import utc_now
from backend.infra.db.models import ToolInvocationModel
from backend.infra.integrations.execution_check import _mapping, _order_id
from backend.infra.integrations.order_valuation_audit import audit_orders
from backend.infra.repositories.exposure_cap_repo import ExposureCapRepository
from backend.stock_api.mx.trading_parser import parse_trade_instruction
from backend.stock_api.public import StockMarketDataService


def approval_statuses(
    orders: list[PortfolioOrderSnapshot],
    calls: list[ToolInvocationModel],
) -> dict[str, str]:
    reviews = {
        (r.run_id, r.tool_call_id): r for r in calls if r.tool_name == "prepare_buy"
    }
    by_order = {o.order_id: o for o in orders}
    statuses = {o.order_id: "not_recorded" for o in orders}
    for call in calls:
        if call.tool_name != "trade":
            continue
        oid = _order_id(_mapping(call.result_json))
        if oid not in by_order:
            continue
        assert oid is not None
        args = _mapping(call.arguments_json)
        review = reviews.get((call.run_id, str(args.get("buy_review_call_id"))))
        # A legacy order has no review. A claimed but invalid link is unknown.
        if review is None:
            if args.get("buy_review_call_id"):
                statuses[oid] = "unknown"
            continue
        statuses[oid] = "unknown"
        result = _mapping(review.result_json)
        try:
            intent = parse_trade_instruction(str(args.get("instruction") or ""))
            checked = datetime.fromisoformat(str(result.get("checked_at")))
            submitted = datetime.fromisoformat(call.created_at)
            age = (submitted - checked).total_seconds()
            order = by_order[oid]
            if (
                call.status == "COMPLETED"
                and review.status == "COMPLETED"
                and result.get("status") == "approved"
                and result.get("run_id") == call.run_id
                and intent.name == "trade"
                and intent.payload["type"] == "buy"
                and result.get("symbol") == order.symbol == intent.payload["stockCode"]
                and result.get("price") == order.order_price == intent.payload["price"]
                and result.get("quantity")
                == order.quantity
                == intent.payload["quantity"]
                and 0 <= age <= 300
            ):
                statuses[oid] = "recorded"
        except (ValueError, TypeError, KeyError):
            pass
    return statuses


class TradingFactsQuery:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        portfolio: PortfolioQueryPort | None,
        market_data: StockMarketDataService | None,
    ):
        self.factory, self.portfolio, self.market_data = factory, portfolio, market_data

    async def collect(self, run_id: int) -> TradingFacts:
        facts = TradingFacts(checked_at=utc_now().isoformat())
        if self.portfolio is None:
            return replace(
                facts, issues=("账户连接不可用，潜在敞口与挂单状态待核对。",)
            )
        clear = getattr(self.portfolio, "clear_read_cache", None)
        if callable(clear):
            clear()
        try:
            snapshot = await self.portfolio.get_account_snapshot()
            orders = await self.portfolio.get_orders()
        except Exception:
            return replace(facts, issues=("账户查询失败，潜在敞口与挂单状态待核对。",))
        issues = []
        try:
            age = (utc_now() - snapshot.captured_at).total_seconds()
            if age < -5 or age > 300:
                raise ValueError("账户观测时间过期或异常")
            amounts = exposure_amounts(snapshot, orders)
            facts = replace(
                facts,
                account_observed_at=snapshot.captured_at.isoformat(),
                total_asset=snapshot.total_asset,
                market_value=snapshot.market_value,
                pending_buy_amount=amounts.pending,
                frozen_cash=snapshot.frozen_cash,
                reserved_buy_amount=amounts.reserved,
                potential_amount=amounts.potential,
                potential_pct=amounts.potential_pct,
            )
        except (ValueError, TypeError):
            issues.append("账户或委托数据无效/过期，潜在敞口待核对。")
        async with self.factory() as session:
            calls = list(
                await session.scalars(
                    select(ToolInvocationModel).where(
                        ToolInvocationModel.tool_name.in_(("prepare_buy", "trade"))
                    )
                )
            )
            repo = ExposureCapRepository(session)
            try:
                day, cap = await repo.daily_limit_for_run(run_id)
                if day != utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date():
                    cap = None
                if cap is not None and math.isfinite(cap) and 0 <= cap <= 100:
                    remaining = (
                        snapshot.total_asset * cap / 100 - facts.potential_amount
                        if facts.potential_amount is not None
                        else None
                    )
                    facts = replace(facts, cap_pct=cap, remaining_amount=remaining)
                else:
                    issues.append("当日有效上限尚未确认，剩余额度不作推算。")
            except ValueError:
                issues.append("运行交易日或当日上限无法确认。")
        active = [
            o
            for o in orders
            if o.direction == "BUY"
            and o.quantity > o.filled_quantity
            and o.status
            in {"PENDING", "PARTIAL", "PENDING_CANCEL", "PARTIAL_PENDING_CANCEL"}
        ]
        reviews = await audit_orders(
            active, self.market_data, approval_statuses(active, calls)
        )
        return replace(
            facts,
            orders=reviews,
            issues=tuple(issues),
            checked_at=utc_now().isoformat(),
        )
