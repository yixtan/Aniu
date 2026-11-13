"""Immutable review context, including legacy cancellations and current rules."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.account import PortfolioOrderSnapshot, PositionSnapshot
from backend.infra.db.models import (
    MemoryItemModel,
    StrategyRunModel,
    ToolInvocationModel,
    ValuationCancellationModel,
)
from backend.stock_api.mx.trading_parser import parse_trade_instruction

RULE_TOPICS = (
    "买入",
    "加仓",
    "承接",
    "试仓",
    "安全垫",
    "仓位",
    "资金",
    "估值",
    "撤单",
    "准入",
    "阻断",
)


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def mapping(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "null")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _report_reason(run: StrategyRunModel | None, order_id: str, symbol: str) -> str:
    if run is None:
        return "旧撤单缺少原因记录，须重新审查"
    text = re.sub(r"<[^>]+>", " ", run.summary or "")
    lines = [
        line.strip()
        for line in text.splitlines()
        if (order_id in line or symbol in line) and ("撤" in line or "取消" in line)
    ]
    return "\n".join(lines[:4])[:1600] or "旧撤单缺少原因记录，须重新审查"


async def review_context(
    factory: async_sessionmaker[AsyncSession],
    symbol: str,
    name: str,
    orders: list[PortfolioOrderSnapshot],
    positions: list[PositionSnapshot],
) -> dict[str, object]:
    held = sum(p.quantity for p in positions if p.symbol == symbol)
    pending = sum(
        o.quantity - o.filled_quantity
        for o in orders
        if o.symbol == symbol
        and o.direction == "BUY"
        and o.status
        in {"PENDING", "PARTIAL", "PENDING_CANCEL", "PARTIAL_PENDING_CANCEL"}
    )
    by_id = {o.order_id: o for o in orders}
    cancellations = []
    async with factory() as session:
        memories = list(
            await session.scalars(
                select(MemoryItemModel)
                .where(MemoryItemModel.deleted_at.is_(None))
                .order_by(MemoryItemModel.id)
            )
        )
        rules = []
        for item in memories:
            if (
                symbol in item.content
                or (name and name in item.content)
                or any(word in item.content for word in RULE_TOPICS)
            ):
                rules.append(
                    {"id": item.id, "version": item.version, "content": item.content}
                )
        calls = await session.scalars(
            select(ToolInvocationModel)
            .where(ToolInvocationModel.tool_name == "cancel")
            .order_by(ToolInvocationModel.created_at)
        )
        for call in calls:
            args = mapping(call.arguments_json)
            try:
                intent = parse_trade_instruction(str(args.get("instruction") or ""))
            except ValueError:
                continue
            if intent.name == "cancel" and intent.payload.get("stockCode") != symbol:
                continue
            result = mapping(call.result_json)
            recorded = result.get("cancellation_review")
            meta = recorded if isinstance(recorded, dict) else {}
            if intent.name == "cancel":
                ids = [str(intent.payload["orderId"])]
            else:
                # A blanket cancellation only concerns orders existing when
                # that request was made. Never assign it to later orders.
                ids = [
                    o.order_id
                    for o in orders
                    if o.symbol == symbol
                    and o.direction == "BUY"
                    and o.submitted_at is not None
                    and o.submitted_at <= datetime.fromisoformat(call.created_at)
                ]
            if intent.name == "cancel_all" and "orders" in meta:
                ids = [
                    str(item["order_id"])
                    for item in meta["orders"]
                    if item["symbol"] == symbol and item["direction"] == "BUY"
                ]
            run = await session.get(StrategyRunModel, call.run_id)
            for oid in ids:
                order = by_id.get(oid)
                if order is not None and order.direction != "BUY":
                    continue
                cancellations.append(
                    {
                        "key": f"{call.run_id}:{call.tool_call_id}:{oid}",
                        "order_id": oid,
                        "run_id": call.run_id,
                        "requested_at": call.created_at,
                        "status": order.status if order is not None else "UNKNOWN",
                        "reason": str(
                            args.get("reason")
                            or meta.get("reason")
                            or _report_reason(run, oid, symbol)
                        ),
                        "origin": str(
                            meta.get("origin")
                            or (
                                "valuation"
                                if args.get("source") == "valuation_policy"
                                else "legacy"
                            )
                        ),
                        "conditions": meta.get("conditions") or {},
                    }
                )
        system = await session.scalars(
            select(ValuationCancellationModel).where(
                ValuationCancellationModel.symbol == symbol
            )
        )
        known_ids = {row["order_id"] for row in cancellations}
        for system_item in system:
            if system_item.order_id in known_ids:
                continue
            order = by_id.get(system_item.order_id)
            cancellations.append(
                {
                    "key": f"valuation:{system_item.order_id}",
                    "order_id": system_item.order_id,
                    "run_id": system_item.triggered_by_run_id,
                    "requested_at": system_item.created_at,
                    "status": order.status if order is not None else "UNKNOWN",
                    "reason": system_item.cause + " " + system_item.explanation,
                    "origin": "valuation",
                    "conditions": {},
                }
            )
    if sum(len(str(rule["content"])) for rule in rules) > 120_000:
        raise ValueError("相关经验超过审查容量，禁止遗漏规则后继续买入")
    return {
        "held_quantity": held,
        "pending_orders": [
            {
                "order_id": o.order_id,
                "price": o.order_price,
                "quantity": o.quantity,
                "filled_quantity": o.filled_quantity,
                "status": o.status,
            }
            for o in orders
            if o.symbol == symbol
            and o.direction == "BUY"
            and o.status
            in {"PENDING", "PARTIAL", "PENDING_CANCEL", "PARTIAL_PENDING_CANCEL"}
        ],
        "pending_buy_quantity": pending,
        "memories": rules,
        "cancellations": cancellations,
    }
