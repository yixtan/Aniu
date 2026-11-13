"""Complete evidence must be read and confirmed before prepare_buy can approve."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.account.ports import PortfolioQueryPort
from backend.business.shared.trading import utc_now
from backend.infra.db.models import ToolInvocationModel
from backend.infra.integrations.buy_research_data import (
    MARKET_ZONE,
    POLICY_VERSION,
    research_data,
)
from backend.infra.integrations.buy_review_context import (
    fingerprint,
    mapping,
    review_context,
)
from backend.llm import AbortSignal
from backend.stock_api.public import StockMarketDataService


async def current_context(
    factory: async_sessionmaker[AsyncSession],
    portfolio: PortfolioQueryPort,
    symbol: str,
    name: str,
) -> dict[str, object]:
    clear = getattr(portfolio, "clear_read_cache", None)
    if callable(clear):
        clear()
    orders = await portfolio.get_orders()
    positions = await portfolio.get_positions()
    return await review_context(factory, symbol, name, orders, positions)


def check_reentry(
    context: dict[str, Any], run_id: int, now: datetime, quote: dict[str, Any]
) -> None:
    today = now.astimezone(MARKET_ZONE).date()
    for item in context["cancellations"]:
        if item["status"] not in {
            "CANCELLED",
            "PARTIAL_CANCELLED",
            "REJECTED",
            "FILLED",
        }:
            raise ValueError("同股既有撤单结果尚未确认，禁止再次买入")
        if item["run_id"] == run_id:
            raise ValueError("同轮已撤销该股买单，须下一轮重新完整研究，禁止改量重挂")
        requested = datetime.fromisoformat(item["requested_at"])
        if requested.tzinfo is None:
            raise ValueError("既有撤单时间无法确认，禁止再次买入")
        day = requested.astimezone(MARKET_ZONE).date()
        if item["origin"] in {"run", "legacy"} and day >= today:
            raise ValueError("当日主动/旧原因撤单仍需跨日重新确认，禁止当天反复买回")
        if item["origin"] == "watch" and day == today:
            conditions = item["conditions"]
            until = conditions.get("cancel_if_unfilled_after")
            if until and now.astimezone(MARKET_ZONE).strftime("%H:%M") >= until:
                raise ValueError("原挂单到期原因仍存在，禁止重新买入")
            price = quote["price"]
            above = conditions.get("cancel_if_price_above")
            below = conditions.get("cancel_if_price_below")
            if (above is not None and price > above) or (
                below is not None and price < below
            ):
                raise ValueError("原挂单价格失效原因尚未消除，禁止重新买入")
            if not any((until, above, below)):
                raise ValueError("原盯盘撤单没有可验证的失效条件，禁止当天买回")


def _required_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not 8 <= len(value.strip()) <= 4000:
        raise ValueError(f"完整决策缺少{name}，须填写具体依据")


async def confirm_research(
    factory: async_sessionmaker[AsyncSession] | None,
    portfolio: PortfolioQueryPort | None,
    run_id: int,
    instruction: str,
    tool_call_id: str,
    review_id: str | None,
    decision: object,
    valuation: dict[str, object],
) -> dict[str, Any]:
    if (
        factory is None
        or portfolio is None
        or not review_id
        or not isinstance(decision, dict)
    ):
        raise ValueError("必须先review_buy并提交完整决策，旧估值审核不能授权买入")
    async with factory() as session:
        row = await session.get(ToolInvocationModel, (run_id, review_id))
        if row is None or row.tool_name != "review_buy" or row.status != "COMPLETED":
            raise ValueError("缺少本轮成功的研究证据收集")
        evidence = mapping(row.result_json)
        confirmations = await session.scalars(
            select(ToolInvocationModel).where(
                ToolInvocationModel.run_id == run_id,
                ToolInvocationModel.tool_name == "prepare_buy",
            )
        )
        for confirmation in confirmations:
            if (
                confirmation.tool_call_id != tool_call_id
                and mapping(confirmation.arguments_json).get("research_review_call_id")
                == review_id
            ):
                raise ValueError("研究证据已用于其他决策确认，必须重新review_buy")
    if (
        evidence.get("policy_version") != POLICY_VERSION
        or evidence.get("instruction") != instruction
        or evidence.get("run_id") != run_id
    ):
        raise ValueError("研究证据与本轮指令不一致")
    age = (
        utc_now() - datetime.fromisoformat(str(evidence["checked_at"]))
    ).total_seconds()
    if not -5 <= age <= 300:
        raise ValueError("研究证据已过期，必须重新审查")
    original = evidence["valuation"]
    for key in (
        "symbol",
        "price",
        "quantity",
        "cancel_if_price_below",
        "cancel_if_price_above",
    ):
        if original.get(key) != valuation.get(key):
            raise ValueError("研究后改价、改量或修改价格条件，必须重新review_buy")
    for key in ("thesis", "counter_evidence", "sizing_rationale"):
        _required_text(decision.get(key), key)
    expected_rules = {(r["id"], r["version"]) for r in evidence["context"]["memories"]}
    reviews = decision.get("memory_reviews")
    if not isinstance(reviews, list) or len(reviews) != len(expected_rules):
        raise ValueError("必须逐条审查相关经验，不能遗漏")
    actual_rules = set()
    for review in reviews:
        if not isinstance(review, dict):
            raise ValueError("经验审查格式无效")
        _required_text(review.get("assessment"), "经验适用性与反证")
        actual_rules.add((review.get("id"), review.get("version")))
    if actual_rules != expected_rules:
        raise ValueError("经验编号或版本不一致，必须重新审查")
    expected_cancels = {r["key"] for r in evidence["context"]["cancellations"]}
    cancellations = decision.get("cancellation_reviews")
    if not isinstance(cancellations, list) or len(cancellations) != len(
        expected_cancels
    ):
        raise ValueError("必须逐笔复核同股历史撤单原因")
    actual_cancels = set()
    for item in cancellations:
        if not isinstance(item, dict):
            raise ValueError("撤单原因复核格式无效")
        _required_text(item.get("resolution"), "撤单原因消除依据")
        actual_cancels.add(item.get("key"))
    if actual_cancels != expected_cancels:
        raise ValueError("撤单记录不一致，必须重新审查")
    current = await current_context(
        factory,
        portfolio,
        str(valuation["symbol"]),
        str(cast(dict[str, Any], valuation["quote"]).get("name") or ""),
    )
    if fingerprint(current) != evidence["context_fingerprint"]:
        raise ValueError("持仓、挂单、相关经验或撤单记录变化，必须重新review_buy")
    check_reentry(current, run_id, utc_now(), cast(dict[str, Any], valuation["quote"]))
    return evidence


async def recheck_complete_review(
    factory: async_sessionmaker[AsyncSession],
    portfolio: PortfolioQueryPort,
    market: StockMarketDataService | None,
    run_id: int,
    review: dict[str, object],
    abort_signal: AbortSignal | None,
    fresh_quote: dict[str, Any],
) -> None:
    if market is None or review.get("policy_version") != POLICY_VERSION:
        raise ValueError("完整买入审查缺失，禁止下单")
    async with factory() as session:
        row = await session.get(
            ToolInvocationModel, (run_id, str(review.get("research_review_call_id")))
        )
        if row is None or row.status != "COMPLETED" or row.tool_name != "review_buy":
            raise ValueError("完整研究证据记录无法确认")
        evidence = mapping(row.result_json)
    quote = cast(dict[str, Any], review["quote"])
    current = await current_context(
        factory, portfolio, str(review["symbol"]), str(quote.get("name") or "")
    )
    if fingerprint(current) != evidence.get("context_fingerprint"):
        raise ValueError("下单前持仓、挂单、经验或撤单记录变化，必须重新完整审查")
    age = (
        utc_now() - datetime.fromisoformat(str(evidence.get("checked_at")))
    ).total_seconds()
    if not -5 <= age <= 300:
        raise ValueError("完整研究证据已过期")
    check_reentry(current, run_id, utc_now(), fresh_quote)
    research = await research_data(
        market, str(review["symbol"]), utc_now(), abort_signal
    )
    if fingerprint(research) != fingerprint(evidence.get("research")):
        raise ValueError("资金或财务事实变化，必须重新完整审查")
