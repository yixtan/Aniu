"""Collect all buy evidence in a tool result before any approval is possible."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.account.ports import PortfolioQueryPort
from backend.business.shared.trading import utc_now
from backend.infra.integrations.buy_decision_review import (
    check_reentry,
    current_context,
)
from backend.infra.integrations.buy_preflight import valuation_review
from backend.infra.integrations.buy_research_data import POLICY_VERSION, research_data
from backend.infra.integrations.buy_review_context import fingerprint
from backend.infra.integrations.tool_policy import SideEffectLevel
from backend.llm import AbortSignal, ProviderJsonObject, ToolDefinition
from backend.stock_api.public import StockMarketDataService


@dataclass(slots=True)
class ReviewBuyTool:
    market_data: StockMarketDataService | None
    portfolio: PortfolioQueryPort | None
    session_factory: async_sessionmaker[AsyncSession] | None
    name: str = "review_buy"
    enabled_stages: tuple[str, ...] = field(default=("Run",))
    side_effect_level: SideEffectLevel = SideEffectLevel.WRITE
    execution_mode: str = "sequential"
    requires_market_open: bool = True

    def is_write_call(self, arguments: object) -> bool:
        return True

    def to_tool_definition(self) -> ToolDefinition:
        return {
            "name": self.name,
            "description": (
                "买入研究前置审查，不下单。确定拟买指令后先调用本工具，系统收集估值、"
                "同来源近两个完整交易日资金、两季财务、当前持仓、相关经验及历史撤单原因。"
                "读完全部证据、确定最终数量和反证后，再调用prepare_buy逐条确认。"
                "修改指令须重新review_buy。缺失/不合格数据拒绝买入。"
            ),
            "parameters": cast(
                ProviderJsonObject,
                {
                    "type": "object",
                    "properties": {
                        "instruction": {"type": "string", "minLength": 1},
                        "cancel_if_price_below": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                        },
                        "cancel_if_price_above": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                        },
                    },
                    "required": ["instruction"],
                    "additionalProperties": False,
                },
            ),
        }

    async def run_for_call(
        self,
        *,
        run_id: int,
        tool_call_id: str,
        instruction: str,
        abort_signal: AbortSignal | None = None,
        cancel_if_price_below: float | None = None,
        cancel_if_price_above: float | None = None,
    ) -> object:
        if (
            self.market_data is None
            or self.portfolio is None
            or self.session_factory is None
        ):
            raise ValueError("完整研究审查缺少数据、账户或持久化连接，禁止买入")
        valuation = await valuation_review(
            self.market_data,
            run_id,
            tool_call_id,
            instruction,
            cancel_if_price_below,
            cancel_if_price_above,
            abort_signal,
        )
        symbol = str(valuation["symbol"])
        quote = cast(dict[str, Any], valuation["quote"])
        context = await current_context(
            self.session_factory, self.portfolio, symbol, str(quote.get("name") or "")
        )
        check_reentry(context, run_id, utc_now(), quote)
        research = await research_data(
            self.market_data, symbol, utc_now(), abort_signal
        )
        return {
            "status": "review_required",
            "policy_version": POLICY_VERSION,
            "run_id": run_id,
            "instruction": instruction,
            "checked_at": utc_now().isoformat(),
            "valuation": valuation,
            "research": research,
            "context": context,
            "context_fingerprint": fingerprint(context),
        }

    async def run(self, **kwargs: object) -> object:
        raise RuntimeError("review_buy requires trusted run call context")
