"""Read-only valuation of existing orders; never creates a buy approval."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.business.account import PortfolioOrderSnapshot
from backend.business.runs.trading_facts import OrderValuationReview
from backend.business.shared.trading.value_objects import utc_now
from backend.infra.integrations.buy_preflight import (
    _positive,
    _quote,
    _thresholds,
    valuation_checks,
)
from backend.stock_api.public import QuoteSnapshotRequest, StockMarketDataService
from backend.stock_api.public.contracts import ValuationRequest

AUDIT_BATCH_TIMEOUT_SECONDS = 25


def unavailable_review(
    order: PortfolioOrderSnapshot, pretrade_status: str
) -> OrderValuationReview:
    return OrderValuationReview(
        order.order_id,
        order.symbol,
        order.stock_name,
        order.order_price,
        order.quantity - order.filled_quantity,
        pretrade_status,
        "unknown",
        None,
        False,
        "估值数据无法确认；只读复核不影响原委托。",
    )


async def audit_order(
    order: PortfolioOrderSnapshot,
    market_data: StockMarketDataService | None,
    *,
    pretrade_status: str = "not_recorded",
) -> OrderValuationReview:
    review = unavailable_review(order, pretrade_status)
    if market_data is None or order.order_price is None:
        return review
    try:
        async with asyncio.timeout(12):
            request = QuoteSnapshotRequest((order.symbol,), detail="full")
            quote, valuation = await asyncio.gather(
                market_data.execute_fresh(request),
                market_data.execute_fresh(ValuationRequest(request.symbols[0])),
            )
            row = _quote(quote, request.symbols[0], allow_stale=True)
            checks = valuation_checks(
                row, _thresholds(valuation, request.symbols[0]), order.order_price
            )
        observed = datetime.fromisoformat(str(row["market_time"]))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        failed = [
            f"{str(c['metric']).upper()} {c['window_years']}年"
            for c in checks
            if not c["passed"]
        ]
        return replace(
            review,
            valuation_status="failed" if failed else "passed",
            observed_at=observed.isoformat(),
            stale=(utc_now() - observed).total_seconds() > 60,
            explanation="未通过：" + "、".join(failed)
            if failed
            else "拟挂价PE/PB八项均不高于30分位阈值。",
            checks=tuple(checks),
            checked_at=utc_now().isoformat(),
            reference_price=_positive(row["price"], "price"),
            reference_pe_ttm=_positive(row["pe_ttm"], "pe_ttm"),
            reference_pb=_positive(row["pb"], "pb"),
        )
    except Exception:
        # Public source errors are not proof of a failed valuation. Do not
        # publish raw provider exceptions or label missing evidence as passed.
        return review


async def audit_orders(
    orders: list[PortfolioOrderSnapshot],
    market_data: StockMarketDataService | None,
    pretrade: dict[str, str] | None = None,
) -> tuple[OrderValuationReview, ...]:
    semaphore = asyncio.Semaphore(2)
    results: dict[str, OrderValuationReview] = {}

    async def one(order: PortfolioOrderSnapshot) -> OrderValuationReview:
        async with semaphore:
            result = await audit_order(
                order,
                market_data,
                pretrade_status=(pretrade or {}).get(order.order_id, "not_recorded"),
            )
            results[order.order_id] = result
            return result

    try:
        async with asyncio.timeout(AUDIT_BATCH_TIMEOUT_SECONDS):
            await asyncio.gather(*(one(order) for order in orders))
    except TimeoutError:
        pass
    return tuple(
        results.get(order.order_id)
        or unavailable_review(
            order, (pretrade or {}).get(order.order_id, "not_recorded")
        )
        for order in orders
    )
