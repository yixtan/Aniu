"""Reads the current order plan for a watch, on its own session."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.account.ports import PortfolioQueryPort
from backend.business.order_directives import OrderDirective
from backend.business.order_directives.handoff import HandoffCheck, check_handoff
from backend.business.runs.trading_facts import TradingFacts
from backend.infra.db.models import ToolInvocationModel
from backend.infra.integrations.execution_check import _mapping, _order_id
from backend.infra.integrations.trading_facts_query import TradingFactsQuery
from backend.infra.repositories import OrderDirectiveRepository
from backend.stock_api.public import StockMarketDataService


@dataclass(frozen=True, slots=True)
class RunOrderPlanQuery:
    """Same reason as the watchlist query: a run holds its session for a while,
    and a list read at the start should not sit inside that transaction."""

    session_factory: async_sessionmaker[AsyncSession]
    portfolio: PortfolioQueryPort | None = None
    market_data: StockMarketDataService | None = None

    async def trading_facts(self, run_id: int) -> TradingFacts:
        return await TradingFactsQuery(
            self.session_factory, self.portfolio, self.market_data
        ).collect(run_id)

    async def check_handoff(self, run_id: int) -> HandoffCheck:
        if self.portfolio is None:
            raise ValueError("portfolio unavailable")
        clear = getattr(self.portfolio, "clear_read_cache", None)
        if callable(clear):
            clear()
        orders = await self.portfolio.get_orders()
        async with self.session_factory() as session:
            directives = tuple(await OrderDirectiveRepository(session).list_current())
            calls = list(
                await session.scalars(
                    select(ToolInvocationModel).where(
                        ToolInvocationModel.run_id == run_id,
                        ToolInvocationModel.tool_name == "trade",
                    )
                )
            )
        report = check_handoff(run_id, orders, directives)
        known_ids = {o.order_id for o in orders}
        unknown_calls = [
            call
            for call in calls
            if (_order_id(_mapping(call.result_json)) not in known_ids)
        ]
        if unknown_calls:
            return HandoffCheck(
                "unknown",
                report.checked_at,
                report.active_count,
                report.covered_count,
                report.issues
                + (
                    "本轮存在未能对应到账户委托的交易请求；结果确认前不能认定交接完整。",
                ),
            )
        return report

    async def current(self) -> tuple[OrderDirective, ...]:
        async with self.session_factory() as session:
            items = await OrderDirectiveRepository(session).list_current()
        return tuple(items)

    async def previous(self) -> tuple[OrderDirective, ...]:
        async with self.session_factory() as session:
            items = await OrderDirectiveRepository(session).list_previous()
        return tuple(items)


__all__ = ["RunOrderPlanQuery"]
