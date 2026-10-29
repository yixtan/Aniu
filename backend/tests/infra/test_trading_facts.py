"""System facts use real ledger links and the execution guard's calculation."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.business.exposure.calculation import exposure_amounts
from backend.business.runs.agent_runner import AgentStageResult
from backend.business.runs.execution import RunReport
from backend.business.runs.run_events import RunEventType
from backend.business.runs.stages.run_stage import RunStage
from backend.business.runs.stages.summary_stage import SummaryStage
from backend.business.runs.trading_facts import TradingFacts
from backend.infra.db.models import ToolInvocationModel
from backend.infra.integrations.order_valuation_audit import audit_order, audit_orders
from backend.infra.integrations.trading_facts_query import TradingFactsQuery
from backend.infra.repositories.tool_invocation_repo import ToolInvocationRepository
from backend.tests.business.test_run_stage import RecordingRunner, _context
from backend.tests.business.test_summary_stage import RecordingRunner as SummaryRunner
from backend.tests.business.test_summary_stage import _context as summary_context
from backend.tests.infra.buy_fixtures import approve, market_service
from backend.tests.infra.test_exposure_execution_guard import (
    clients,
    declare,
    order,
    seed,
    snapshot,
)


async def seeded(factory):
    await seed(factory, 1, datetime.now(UTC).isoformat())
    await declare(factory, 1, 20)


@pytest.mark.parametrize("frozen,expected", [(0, 5500), (3000, 6000)])
def test_partial_orders_and_frozen_cash_are_not_added_twice(frozen, expected):
    orders = [
        order(order_price=10, quantity=400, filled_quantity=150, status="PARTIAL"),
        order(order_id="sell", direction="SELL", quantity=1000),
        order(order_id="closed", status="CANCELLED"),
    ]
    amounts = exposure_amounts(
        replace(snapshot(), market_value=3000, frozen_cash=frozen), orders
    )
    assert amounts.pending == 2500
    assert amounts.potential == expected
    assert amounts.potential_pct == expected / 1000


@pytest.mark.parametrize("case", ["pass", "fail", "error", "stale"])
async def test_read_only_audit_never_becomes_a_pretrade_approval(case):
    service = market_service(pe=1000 if case == "fail" else 10)
    if case == "error":
        service.execute_fresh.side_effect = RuntimeError("private upstream error")
    if case == "stale":
        original = service.execute_fresh.side_effect

        async def aged(request, abort=None):
            result = await original(request, abort)
            if "quotes" in result["data"]:
                result["data"]["quotes"][0]["market_time"] = (
                    datetime.now(UTC) - timedelta(hours=2)
                ).isoformat()
            return result

        service.execute_fresh.side_effect = aged
    report = await audit_order(order(order_price=10), service)
    assert report.pretrade_status == "not_recorded"
    assert (
        report.valuation_status
        == {"pass": "passed", "stale": "passed", "fail": "failed", "error": "unknown"}[
            case
        ]
    )
    assert report.stale == (case == "stale")
    assert "private" not in report.explanation


async def test_audit_timeout_keeps_finished_results_and_cancels_pending_reads(
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.infra.integrations.order_valuation_audit.AUDIT_BATCH_TIMEOUT_SECONDS",
        0.05,
    )
    service = market_service()
    original = service.execute_fresh.side_effect
    cancelled = asyncio.Event()

    async def slow(request, abort=None):
        symbol = getattr(request, "symbol", None) or request.symbols[0]
        if symbol == "600001.SH":
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return await original(request, abort)

    service.execute_fresh.side_effect = slow
    rows = await audit_orders(
        [
            order(order_id="finished", order_price=10),
            order(order_id="slow", symbol="600001", order_price=10),
        ],
        service,
    )
    assert [(r.order_id, r.valuation_status) for r in rows] == [
        ("finished", "passed"),
        ("slow", "unknown"),
    ]
    assert cancelled.is_set()


async def test_collector_verifies_actual_approval_links_and_never_writes_ledger(
    session_factory,
):
    await seeded(session_factory)
    service = await approve(session_factory, 1, "买入 600000 10 100")
    async with session_factory() as session:
        repo = ToolInvocationRepository(session)
        await repo.reserve(
            run_id=1,
            tool_call_id="buy",
            tool_name="trade",
            arguments={
                "instruction": "买入 600000 10 100",
                "buy_review_call_id": "review",
            },
        )
        await repo.complete(
            run_id=1, tool_call_id="buy", result={"data": {"orderId": "new"}}
        )
        await session.commit()
        before = list(await session.scalars(select(ToolInvocationModel)))
        saved = [(r.run_id, r.tool_call_id, r.status, r.result_json) for r in before]
    p, _ = clients(
        replace(snapshot(), market_value=3000, frozen_cash=3000),
        [
            order(order_id="new", order_price=10),
            order(
                order_id="old",
                symbol="600001",
                order_price=10,
                quantity=200,
                filled_quantity=50,
                status="PARTIAL",
            ),
        ],
    )
    facts = await TradingFactsQuery(session_factory, p, service).collect(1)
    assert facts.potential_amount == 6000
    assert facts.potential_pct == 6
    assert facts.remaining_amount == 14000
    assert [
        (r.order_id, r.pretrade_status, r.valuation_status) for r in facts.orders
    ] == [("new", "recorded", "passed"), ("old", "not_recorded", "passed")]
    assert "全部成交后不再重复加一次" in facts.markdown()
    assert "无下单前审核记录" in facts.markdown()
    async with session_factory() as session:
        after = list(await session.scalars(select(ToolInvocationModel)))
        assert saved == [
            (r.run_id, r.tool_call_id, r.status, r.result_json) for r in after
        ]


@pytest.mark.parametrize("case", ["failure", "stale", "unknown-order"])
async def test_missing_evidence_is_not_zero_exposure(session_factory, case):
    await seeded(session_factory)
    p, _ = clients(replace(snapshot(), market_value=3000, frozen_cash=0), [])
    if case == "failure":
        p.get_orders.side_effect = RuntimeError("private database password")
    if case == "stale":
        p.get_account_snapshot.return_value = replace(
            snapshot(), captured_at=datetime.now(UTC) - timedelta(hours=1)
        )
    if case == "unknown-order":
        p.get_orders.return_value = [order(direction="UNKNOWN")]
    facts = await TradingFactsQuery(session_factory, p, market_service()).collect(1)
    assert facts.potential_amount is None
    assert "潜在敞口：待核对" in facts.markdown()
    assert "private" not in facts.markdown()


async def test_run_adds_authoritative_facts_without_another_model_or_trade_call():
    from unittest.mock import AsyncMock

    ctx = _context(market_open=True)
    facts = TradingFacts(datetime.now(UTC).isoformat(), issues=("合成证据待核对",))
    ctx.trading_facts_reader = AsyncMock(return_value=facts)
    ctx.tool_loop_event_sink = AsyncMock()
    runner = RecordingRunner(AgentStageResult(content="模型声称已审核"))
    report = await RunStage().execute(ctx, runner)
    assert report.system_facts == facts
    assert report.content.endswith(facts.markdown())
    assert report.model_content.startswith("模型声称已审核")
    assert "合成证据待核对" not in report.model_content
    assert len(runner.prompts) == 1
    assert (
        ctx.tool_loop_event_sink.call_args.args[1]
        == RunEventType.TRADING_FACTS_COLLECTED
    )


async def test_summary_cannot_rewrite_or_drop_system_facts():
    facts = TradingFacts(datetime.now(UTC).isoformat(), issues=("独立系统证据",))
    report = RunReport(
        content="投资判断" + facts.markdown(),
        model_content="投资判断",
        system_facts=facts,
    )
    runner = SummaryRunner("<p>只排版分析</p>")
    result = await SummaryStage().execute(summary_context(report), runner)
    assert "独立系统证据" not in runner.prompts[0]
    assert result.summary == "<p>只排版分析</p>" + facts.markdown()
    assert json.loads(json.dumps(facts.payload()))["issues"] == ["独立系统证据"]
