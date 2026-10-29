"""Deterministic valuation cancellation, independent of model-written plans."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.account import PortfolioOrderSnapshot
from backend.business.account.ports import PortfolioQueryPort
from backend.business.order_directives.valuation_cancellation import (
    TERMINAL_CANCELLATIONS,
    CancellationObservation,
    ValuationCancellationReport,
)
from backend.business.shared.trading.value_objects import utc_now
from backend.infra.db.models import ValuationCancellationModel, utc_now_iso
from backend.infra.integrations.order_valuation_audit import audit_orders
from backend.infra.repositories.tool_invocation_repo import ToolInvocationRepository
from backend.infra.repositories.valuation_cancellation_repo import (
    ValuationCancellationRepository,
)
from backend.llm import AbortSignal
from backend.stock_api import MxPaperTradingClient, json_safe
from backend.stock_api.mx.trading_parser import parse_trade_instruction
from backend.stock_api.public import StockMarketDataService

ACTIVE = {"PENDING", "PARTIAL", "PENDING_CANCEL", "PARTIAL_PENDING_CANCEL"}
CANCELLABLE = {"PENDING", "PARTIAL"}
TERMINAL = {"FILLED", "CANCELLED", "PARTIAL_CANCELLED", "REJECTED"}


class CancellationNotSent(RuntimeError):
    """The final HTTP guard refused before any request bytes were sent."""


def observation(row: ValuationCancellationModel) -> CancellationObservation:
    return CancellationObservation(
        row.order_id,
        row.symbol,
        row.stock_name,
        row.state,
        row.cause,
        row.explanation,
        row.filled_quantity,
        row.last_observed_at,
        str(row.evidence.get("explanation") or ""),
        str(row.evidence["observed_at"]) if row.evidence.get("observed_at") else None,
    )


def same_order(row: ValuationCancellationModel, order: PortfolioOrderSnapshot) -> bool:
    return (
        order.symbol == row.symbol
        and order.direction == "BUY"
        and order.quantity == row.quantity
        and order.order_price == row.order_price
        and order.submitted_at
        == (datetime.fromisoformat(row.submitted_at) if row.submitted_at else None)
        and 0 <= order.filled_quantity <= order.quantity
    )


class ValuationCancellationService:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        portfolio: PortfolioQueryPort,
        trading: MxPaperTradingClient,
        market_data: StockMarketDataService | None,
    ):
        self.factory, self.portfolio, self.trading, self.market_data = (
            factory,
            portfolio,
            trading,
            market_data,
        )

    async def _orders(self) -> list[PortfolioOrderSnapshot]:
        clear = getattr(self.portfolio, "clear_read_cache", None)
        if callable(clear):
            clear()
        async with asyncio.timeout(10):
            return await self.portfolio.get_orders()

    async def cycle(
        self,
        run_id: int,
        abort_signal: AbortSignal | None,
        cancellations_accepted: Callable[[], bool] | None,
    ) -> ValuationCancellationReport:
        if abort_signal is not None:
            abort_signal.throw_if_aborted()
        try:
            orders = await self._orders()
        except Exception:
            async with self.factory() as session:
                pending = await ValuationCancellationRepository(session).outstanding()
            return ValuationCancellationReport(
                items=tuple(observation(row) for row in pending),
                issues=("账户委托查询失败，未发送新的撤单请求；系统意图保留待核对。",),
            )
        counts = Counter(o.order_id for o in orders)
        by_id = {o.order_id: o for o in orders if counts[o.order_id] == 1}
        touched: set[str] = set()
        async with self.factory() as session:
            pending = await ValuationCancellationRepository(session).outstanding()
            for row in pending:
                touched.add(row.order_id)
                self._reconcile(row, by_id.get(row.order_id))
            await session.commit()
        active = [
            o
            for o in orders
            if o.direction == "BUY"
            and o.status in ACTIVE
            and o.quantity > o.filled_quantity
            and counts[o.order_id] == 1
            and o.order_id not in touched
        ]
        reviews = await audit_orders(active, self.market_data)
        retry_orders = [
            o
            for o, r in zip(active, reviews, strict=True)
            if r.valuation_status == "unknown" or r.stale
        ]
        retries = {
            r.order_id: r for r in await audit_orders(retry_orders, self.market_data)
        }
        async with self.factory() as session:
            repo = ValuationCancellationRepository(session)
            for order, first in zip(active, reviews, strict=True):
                review = retries.get(order.order_id, first)
                if review.valuation_status == "passed" and not review.stale:
                    continue
                cause = (
                    "VALUATION_FAILED"
                    if review.valuation_status == "failed" and not review.stale
                    else "UNVERIFIABLE"
                )
                if cause == "UNVERIFIABLE":
                    review = replace(
                        review,
                        explanation="两次读取后仍缺少新鲜完整估值证据，保护性撤单。",
                    )
                await repo.ensure(order, review, cause, run_id)
                touched.add(order.order_id)
            await session.commit()
        activity = []
        for oid in sorted(touched):
            if abort_signal is not None:
                abort_signal.throw_if_aborted()
            item = await self._act(oid, run_id, abort_signal, cancellations_accepted)
            if item is not None:
                activity.append(item)
        async with self.factory() as session:
            rows = list(
                await session.scalars(
                    select(ValuationCancellationModel).where(
                        ValuationCancellationModel.order_id.in_(touched)
                    )
                )
            )
        issues = (
            ("委托编号重复，相关订单未自动撤销，请核对。",)
            if any(n > 1 for n in counts.values())
            else ()
        )
        return ValuationCancellationReport(
            tuple(observation(r) for r in rows), issues, tuple(activity)
        )

    def _reconcile(
        self, row: ValuationCancellationModel, order: PortfolioOrderSnapshot | None
    ) -> None:
        if row.state in TERMINAL_CANCELLATIONS:
            return
        row.updated_at = utc_now_iso()
        if order is None or not same_order(row, order):
            if row.state != "REQUIRED":
                row.state = "UNKNOWN"
            row.explanation = "无法唯一关联原委托，未确认撤销，不自动重发。"
            return
        row.filled_quantity = order.filled_quantity
        row.last_observed_at = utc_now_iso()
        if order.status == "FILLED" and order.filled_quantity != order.quantity:
            row.state = "UNKNOWN"
            row.explanation = "委托成交状态与数量不一致，待核对。"
            return
        if order.status in ACTIVE and order.filled_quantity >= order.quantity:
            row.state = "UNKNOWN"
            row.explanation = "有效委托没有可撤余量，状态与数量不一致，待核对。"
            return
        if order.status in TERMINAL:
            row.state = (
                order.status
                if order.status in {"CANCELLED", "PARTIAL_CANCELLED"}
                else "CLOSED"
            )
        elif order.status in {"PENDING_CANCEL", "PARTIAL_PENDING_CANCEL"}:
            row.state = "PENDING_CANCEL"
        elif row.state == "SUBMITTING":
            row.state = "UNKNOWN"
            row.explanation = "请求可能已发送，结果尚未确认，不自动重发。"
        elif order.status not in ACTIVE:
            row.state = "UNKNOWN"
            row.explanation = "委托状态不明确，不自动重发。"

    async def _act(
        self,
        oid: str,
        run_id: int,
        abort_signal: AbortSignal | None,
        accepted: Callable[[], bool] | None,
    ) -> dict[str, object] | None:
        async with self.factory() as session:
            row = await session.get(ValuationCancellationModel, oid)
            if row is None or row.state != "REQUIRED":
                return None
        try:
            orders = await self._orders()
        except Exception:
            return None
        matches = [o for o in orders if o.order_id == oid]
        current = matches[0] if len(matches) == 1 else None
        async with self.factory() as session:
            row = await session.get(ValuationCancellationModel, oid)
            assert row is not None
            self._reconcile(row, current)
            await session.commit()
            if (
                row.state != "REQUIRED"
                or current is None
                or not same_order(row, current)
            ):
                return None
        if (
            current.status not in CANCELLABLE
            or not callable(accepted)
            or not accepted()
        ):
            return None
        try:
            parse_trade_instruction(f"撤单 {oid} {row.symbol}")
        except ValueError:
            return None
        if (
            current.submitted_at is None
            or current.submitted_at.tzinfo is None
            or current.submitted_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
            != utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date()
        ):
            async with self.factory() as session:
                row = await session.get(ValuationCancellationModel, oid)
                assert row is not None
                row.explanation = (
                    "委托提交时间无法确认或不属于今日，等待核对，不按编号猜测执行。"
                )
                await session.commit()
            return None
        if abort_signal is not None:
            abort_signal.throw_if_aborted()
        async with self.factory() as session:
            row = await ValuationCancellationRepository(session).claim(oid, run_id)
            if row is None:
                return None
            call_id = row.request_call_id
            await session.commit()
        assert call_id is not None
        instruction = f"撤单 {oid} {row.symbol}"
        sent = False
        result: object = None
        try:
            if abort_signal is not None:
                abort_signal.throw_if_aborted()
            if not accepted():
                await self._finish(
                    oid,
                    run_id,
                    call_id,
                    "REQUIRED",
                    None,
                    "可撤时段已结束，未发送请求。",
                )
                return None
            sent = True

            def before_send() -> None:
                if (
                    abort_signal is not None and abort_signal.aborted
                ) or not accepted():
                    raise CancellationNotSent(
                        "撤单时段已结束或任务已停止，未发送请求。"
                    )

            result = await self.trading.cancel(instruction, before_send=before_send)
        except BaseException as exc:
            # Cancellation of an in-flight HTTP request cannot prove it was
            # not received. Persisted SUBMITTING already forbids replay even
            # if this finalization is itself interrupted by process death.
            await self._finish(
                oid,
                run_id,
                call_id,
                "UNKNOWN"
                if sent and not isinstance(exc, CancellationNotSent)
                else "REQUIRED",
                None,
                "请求结果未知，不自动重发。"
                if sent and not isinstance(exc, CancellationNotSent)
                else "请求尚未发送，等待后续可撤时段。",
            )
            if abort_signal is not None:
                abort_signal.throw_if_aborted()
            if not isinstance(exc, Exception):
                raise
        else:
            await self._finish(
                oid,
                run_id,
                call_id,
                "SUBMITTED",
                result,
                "撤单请求已受理，等待账户确认。",
            )
        # Confirm against a new order observation, never the response alone.
        try:
            fresh = await self._orders()
            found = [o for o in fresh if o.order_id == oid]
            async with self.factory() as session:
                row = await session.get(ValuationCancellationModel, oid)
                assert row is not None
                self._reconcile(row, found[0] if len(found) == 1 else None)
                await session.commit()
        except Exception:
            pass
        return {
            "tool_name": "cancel",
            "tool_call_id": call_id,
            "status": "ok" if result is not None else "error",
            "arguments": {"instruction": instruction},
            "content": json_safe(result),
        }

    async def _finish(
        self,
        oid: str,
        run_id: int,
        call_id: str,
        state: str,
        result: object,
        explanation: str,
    ) -> None:
        async with self.factory() as session:
            row = await session.get(ValuationCancellationModel, oid)
            assert row is not None
            if row.state not in TERMINAL_CANCELLATIONS:
                row.state = state
                row.explanation = explanation
                row.updated_at = utc_now_iso()
            repo = ToolInvocationRepository(session)
            if result is not None:
                await repo.complete(
                    run_id=run_id, tool_call_id=call_id, result=json_safe(result)
                )
            else:
                await repo.fail(run_id=run_id, tool_call_id=call_id, error=explanation)
            await session.commit()
