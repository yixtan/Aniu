"""A buy needs source-backed valuation evidence before any order is sent.

Approvals live in the existing durable tool ledger, scoped to one run and one
exact order. The model proposes a price and invalidation conditions; it cannot
supply the valuation numbers or a self-declared passing verdict.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.order_directives.buy_conditions import check_buy_conditions
from backend.business.shared.trading.value_objects import utc_now
from backend.infra.db.models import ToolInvocationModel
from backend.infra.integrations.execution_check import _mapping
from backend.infra.integrations.tool_policy import SideEffectLevel
from backend.llm import AbortSignal, ProviderJsonObject, ToolDefinition
from backend.stock_api.mx.trading_parser import parse_trade_instruction
from backend.stock_api.public import QuoteSnapshotRequest, StockMarketDataService
from backend.stock_api.public.contracts import ValuationRequest


def _positive(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{label}缺失或无效，禁止买入")
    return float(value)


def _buy(instruction: str) -> tuple[str, float, int]:
    intent = parse_trade_instruction(instruction)
    if intent.name != "trade" or intent.payload["type"] != "buy":
        raise ValueError("prepare_buy只接受限价买入指令")
    price = _positive(intent.payload["price"], "买价")
    quantity = intent.payload["quantity"]
    assert isinstance(quantity, int)
    return str(intent.payload["stockCode"]), price, quantity


def _thresholds(result: dict[str, object], symbol: str) -> list[dict[str, object]]:
    data = result.get("data")
    if not isinstance(data, dict) or data.get("symbol") != symbol:
        raise ValueError("估值证据标的不匹配，禁止买入")
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("缺少估值阈值证据，禁止买入")
    values: dict[tuple[str, int], float] = {}
    for row in items:
        if not isinstance(row, dict) or row.get("metric") not in {"pe", "pb"}:
            continue
        years = row.get("window_years")
        if (
            isinstance(years, bool)
            or not isinstance(years, int)
            or years not in (1, 3, 5, 10)
        ):
            raise ValueError("估值窗口无效，禁止买入")
        key = (str(row["metric"]), years)
        if key in values:
            raise ValueError("估值窗口重复，禁止买入")
        values[key] = _positive(row.get("percentile_30_threshold"), "30分位阈值")
    expected = {(metric, years) for metric in ("pe", "pb") for years in (1, 3, 5, 10)}
    if set(values) != expected:
        raise ValueError("PE/PB近1、3、5、10年估值证据不完整，禁止买入")
    return [
        {"metric": metric, "window_years": years, "percentile_30_threshold": value}
        for (metric, years), value in sorted(values.items())
    ]


def _quote(result: dict[str, object], symbol: str) -> dict[str, object]:
    data = result.get("data")
    quotes = data.get("quotes") if isinstance(data, dict) else None
    if not isinstance(quotes, list) or len(quotes) != 1:
        raise ValueError("缺少唯一行情证据，禁止买入")
    row = quotes[0]
    if not isinstance(row, dict) or row.get("symbol") != symbol:
        raise ValueError("行情标的不匹配，禁止买入")
    try:
        moment = datetime.fromisoformat(str(row.get("market_time")))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    except ValueError as exc:
        raise ValueError("行情观测时间无效，禁止买入") from exc
    age = (utc_now() - moment).total_seconds()
    if age < -5 or age > 60:
        raise ValueError("行情过期或时间异常，禁止买入")
    for name in ("price", "pe_ttm", "pb"):
        _positive(row.get(name), name)
    return cast(dict[str, object], row)


def check_valuation(
    quote: dict[str, object], thresholds: list[dict[str, object]], price: float
) -> list[dict[str, object]]:
    """Compare at the proposed limit price, never dynamic/forward PE."""
    spot = _positive(quote.get("price"), "行情价格")
    checks = []
    failures = []
    for row in thresholds:
        metric = str(row["metric"])
        multiple = _positive(quote.get("pe_ttm" if metric == "pe" else "pb"), metric)
        at_limit = multiple * price / spot
        threshold = _positive(row.get("percentile_30_threshold"), "30分位阈值")
        checks.append({**row, "at_limit": at_limit})
        if at_limit > threshold + 1e-8:
            failures.append(f"{metric.upper()} {row['window_years']}年")
    if failures:
        raise ValueError("拟买价未通过30分位估值门：" + "、".join(failures))
    return checks


@dataclass(slots=True)
class PrepareBuyTool:
    market_data: StockMarketDataService | None
    name: str = "prepare_buy"
    enabled_stages: tuple[str, ...] = field(default=("Run",))
    # Persist the source evidence with the ordinary idempotent tool ledger.
    side_effect_level: SideEffectLevel = SideEffectLevel.WRITE
    execution_mode: str = "sequential"
    requires_market_open: bool = True

    def is_write_call(self, arguments: object) -> bool:
        del arguments
        return True

    def to_tool_definition(self) -> ToolDefinition:
        return {
            "name": self.name,
            "description": (
                "买入前审核，不下单。先完成研究，再提交拟买指令和撤单失效条件。"
                "系统直接查询行情PE(TTM)/PB与近1/3/5/10年30分位阈值，"
                "按拟买价逐项计算；缺失、超阈值或买价与失效条件矛盾均拒绝。"
                "通过后把返回的buy_review_call_id交给trade，五分钟内一次有效，"
                "只适用于本轮完全相同的股票、价格、数量；trade还会复核新行情。"
                "条件必须与之后declare_order_plan一致；没有价格条件可省略。"
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
        abort_signal: AbortSignal | None = None,
        instruction: str,
        cancel_if_price_below: float | None = None,
        cancel_if_price_above: float | None = None,
    ) -> object:
        symbol, price, quantity = _buy(instruction)
        check_buy_conditions(
            price, below=cancel_if_price_below, above=cancel_if_price_above
        )
        if self.market_data is None:
            raise ValueError("缺少估值数据连接，禁止买入")
        request = QuoteSnapshotRequest((symbol,), detail="full")
        valuation, quote = await asyncio.gather(
            self.market_data.execute_fresh(
                ValuationRequest(request.symbols[0]), abort_signal
            ),
            self.market_data.execute_fresh(request, abort_signal),
        )
        thresholds = _thresholds(valuation, request.symbols[0])
        row = _quote(quote, request.symbols[0])
        checks = check_valuation(row, thresholds, price)
        if abort_signal is not None:
            abort_signal.throw_if_aborted()
        return {
            "status": "approved",
            "buy_review_call_id": tool_call_id,
            "run_id": run_id,
            "symbol": symbol,
            "price": price,
            "quantity": quantity,
            "cancel_if_price_below": cancel_if_price_below,
            "cancel_if_price_above": cancel_if_price_above,
            "checked_at": utc_now().isoformat(),
            "quote": row,
            "checks": checks,
        }

    async def run(self, **_: object) -> object:
        raise RuntimeError("prepare_buy requires trusted run call context")


async def load_buy_review(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: int,
    tool_call_id: str,
    review_id: str | None,
    instruction: str,
) -> dict[str, object]:
    if not review_id:
        raise ValueError("买入前必须调用prepare_buy，通过后提供buy_review_call_id")
    async with factory() as session:
        rows = list(
            await session.scalars(
                select(ToolInvocationModel).where(
                    ToolInvocationModel.run_id == run_id,
                    ToolInvocationModel.tool_name.in_(("prepare_buy", "trade")),
                )
            )
        )
    review = next((r for r in rows if r.tool_call_id == review_id), None)
    if (
        review is None
        or review.tool_name != "prepare_buy"
        or review.status != "COMPLETED"
    ):
        raise ValueError("缺少本轮成功的买入审核，禁止下单")
    result = _mapping(review.result_json)
    if result.get("status") != "approved" or result.get("run_id") != run_id:
        raise ValueError("买入审核未通过，禁止下单")
    symbol, price, quantity = _buy(instruction)
    if (result.get("symbol"), result.get("price"), result.get("quantity")) != (
        symbol,
        price,
        quantity,
    ):
        raise ValueError("买入指令与审核不一致，改价改量后必须重新审核")
    try:
        checked = datetime.fromisoformat(str(result.get("checked_at")))
        age = (utc_now() - checked).total_seconds()
    except (ValueError, TypeError) as exc:
        raise ValueError("买入审核时间无效，禁止下单") from exc
    if age < -5 or age > 300:
        raise ValueError("买入审核已过期，请重新调用prepare_buy")
    for row in rows:
        if row.tool_name == "trade" and row.tool_call_id != tool_call_id:
            if _mapping(row.arguments_json).get("buy_review_call_id") == review_id:
                raise ValueError("买入审核已经使用，结果未知也不能重复下单")
    check_buy_conditions(
        price,
        below=result.get("cancel_if_price_below"),
        above=result.get("cancel_if_price_above"),
    )
    return result


async def recheck_buy_review(
    market_data: StockMarketDataService | None,
    review: dict[str, object],
    abort_signal: AbortSignal | None,
) -> None:
    if market_data is None:
        raise ValueError("缺少估值数据连接，禁止买入")
    request = QuoteSnapshotRequest((str(review["symbol"]),), detail="full")
    result = await market_data.execute_fresh(request, abort_signal)
    thresholds = _thresholds(
        {
            "data": {
                "symbol": request.symbols[0],
                "items": review.get("checks"),
            }
        },
        request.symbols[0],
    )
    check_valuation(
        _quote(result, request.symbols[0]),
        thresholds,
        _positive(review.get("price"), "审核买价"),
    )
