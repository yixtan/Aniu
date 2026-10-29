"""A model cannot buy first and inspect eligibility after the side effect."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.business.order_directives import DirectiveAction, OrderDirective
from backend.business.order_directives.handoff import check_handoff
from backend.infra.db.models import ToolInvocationModel
from backend.infra.integrations.agent_runner import _StageToolRegistry
from backend.infra.integrations.agent_runtime import AgentRuntimeFactory
from backend.infra.integrations.buy_preflight import (
    PrepareBuyTool,
    check_buy_conditions,
)
from backend.infra.integrations.exposure_trade_guard import check_same_day_chase
from backend.infra.integrations.mx_agent_tools import TradeTool
from backend.infra.integrations.order_directive_agent_tools import DeclareOrderPlanTool
from backend.infra.repositories.tool_invocation_repo import ToolInvocationRepository
from backend.stock_api.public import QuoteSnapshotRequest
from backend.stock_api.public.contracts import ValuationRequest
from backend.stock_api.public.normalizers.common import normalize_datetime
from backend.stock_api.public.normalizers.market import normalize_quotes
from backend.stock_api.public.providers.tencent import _parse_quotes
from backend.tests.infra.buy_fixtures import approve, market_service
from backend.tests.infra.test_exposure_execution_guard import (
    NOW,
    clients,
    declare,
    order,
    seed,
    snapshot,
)

BUY = "买入 600000 10 100"


@pytest.fixture(autouse=True)
def exposure_clock(monkeypatch):
    monkeypatch.setattr(
        "backend.infra.integrations.exposure_trade_guard.utc_now", lambda: NOW
    )


@pytest.mark.parametrize(
    "price,below,above",
    [
        (10, 10, None),
        (10, 11, None),
        (10, None, 9),
        (10, None, 10),
        (10, float("nan"), None),
        (10, True, None),
    ],
)
def test_contradictory_buy_and_invalidation_are_refused(price, below, above):
    with pytest.raises(ValueError):
        check_buy_conditions(price, below=below, above=above)


@pytest.mark.parametrize("status", ["CANCELLED", "PARTIAL_CANCELLED", "PENDING_CANCEL"])
def test_same_run_cancel_does_not_allow_higher_bid_even_for_different_size(status):
    recent = order(status=status, submitted_at=NOW, order_price=10, quantity=400)
    with pytest.raises(ValueError, match="不得高价追买"):
        check_same_day_chase(
            [recent],
            started_at=NOW - timedelta(minutes=5),
            symbol="600000",
            price=10.01,
        )
    check_same_day_chase(
        [recent], started_at=NOW - timedelta(minutes=5), symbol="600000", price=9.99
    )


@pytest.mark.parametrize(
    "case",
    [
        "missing-window",
        "duplicate",
        "wrong-stock",
        "negative-pe",
        "nan-threshold",
        "missing-pe",
        "stale-quote",
        "pb-fails",
        "pe-fails",
        "no-source",
    ],
)
async def test_prepare_rejects_incomplete_or_failed_source_evidence(case):
    service = market_service()
    original = service.execute_fresh.side_effect

    async def changed(request, abort=None):
        result = await original(request, abort)
        if isinstance(request, ValuationRequest):
            if case == "missing-window":
                result["data"]["items"].pop()
            if case == "duplicate":
                result["data"]["items"].append(result["data"]["items"][0])
            if case == "wrong-stock":
                result["data"]["symbol"] = "600001.SH"
            if case == "nan-threshold":
                result["data"]["items"][0]["percentile_30_threshold"] = float("nan")
        else:
            row = result["data"]["quotes"][0]
            if case == "negative-pe":
                row["pe_ttm"] = -1
            if case == "missing-pe":
                row.pop("pe_ttm")
            if case == "stale-quote":
                row["market_time"] = (
                    datetime.now(UTC) - timedelta(minutes=5)
                ).isoformat()
            if case == "pb-fails":
                row["pb"] = 10
            if case == "pe-fails":
                row["pe_ttm"] = 100
        return result

    service.execute_fresh.side_effect = changed
    with pytest.raises(ValueError):
        await PrepareBuyTool(None if case == "no-source" else service).run_for_call(
            run_id=1,
            tool_call_id="review",
            instruction=BUY,
        )


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "wrong-run",
        "wrong-price",
        "wrong-size",
        "expired",
        "used",
        "fresh-fails",
    ],
)
async def test_trade_cannot_bypass_or_reuse_review(session_factory, case):
    await seed(session_factory, 1)
    await seed(session_factory, 2)
    await declare(session_factory, 1, 20)
    service = await approve(session_factory, 2 if case == "wrong-run" else 1, BUY)
    if case in {"expired", "used"}:
        async with session_factory() as session:
            if case == "expired":
                row = await session.scalar(
                    select(ToolInvocationModel).where(
                        ToolInvocationModel.tool_name == "prepare_buy"
                    )
                )
                data = json.loads(row.result_json)
                data["checked_at"] = (
                    datetime.now(UTC) - timedelta(minutes=6)
                ).isoformat()
                row.result_json = json.dumps(data)
            else:
                await ToolInvocationRepository(session).reserve(
                    run_id=1,
                    tool_call_id="previous",
                    tool_name="trade",
                    arguments={"instruction": BUY, "buy_review_call_id": "review"},
                )
            await session.commit()
    if case == "fresh-fails":
        service = market_service(pe=100)
    p, t = clients(replace(snapshot(), market_value=0, frozen_cash=0))
    tool = TradeTool(t, p, session_factory=session_factory, market_data=service)
    instruction = (
        BUY.replace("10 100", "11 100" if case == "wrong-price" else "10 200")
        if case in {"wrong-price", "wrong-size"}
        else BUY
    )
    with pytest.raises(ValueError):
        await tool.run_for_call(
            run_id=1,
            tool_call_id="buy",
            instruction=instruction,
            buy_review_call_id=None if case == "missing" else "review",
        )
    t.trade.assert_not_awaited()


async def test_real_registry_persists_review_executes_once_and_binds_handoff(
    session_factory,
):
    await seed(session_factory, 1)
    await declare(session_factory, 1, 20)
    p, t = clients(replace(snapshot(), market_value=0, frozen_cash=0))
    t.trade.return_value = {"data": {"orderId": "test"}}
    service = market_service()
    registry = await AgentRuntimeFactory(
        mx_research_client=object(),
        mx_portfolio_client=p,
        mx_trading_client=t,
        public_stock_data=service,
        session_factory=session_factory,
    ).build_tool_registry()
    bound = _StageToolRegistry(
        registry, "Run", run_id=1, invocation_session_factory=session_factory
    )
    approved = await bound.call_idempotently(
        "prepare_buy",
        tool_call_id="review",
        abort_signal=None,
        instruction=BUY,
        cancel_if_price_below=9,
    )
    assert approved["status"] == "approved"
    assert len(approved["checks"]) == 8
    for _ in range(2):
        await bound.call_idempotently(
            "trade",
            tool_call_id="buy",
            abort_signal=None,
            instruction=BUY,
            buy_review_call_id="review",
        )
    t.trade.assert_awaited_once()
    # A different tool-call ID cannot spend the same approval again.
    with pytest.raises(ValueError, match="已经使用"):
        await bound.call_idempotently(
            "trade",
            tool_call_id="duplicate",
            abort_signal=None,
            instruction=BUY,
            buy_review_call_id="review",
        )
    p.get_orders.return_value = [order(order_price=10)]
    plan = dict(
        order_id="test",
        symbol="600000",
        action="hold",
        note="保留",
        cancel_if_price_below=9,
    )
    tool = DeclareOrderPlanTool(session_factory, portfolio=p)
    await tool.run_for_call(run_id=1, tool_call_id="plan", directives=[plan])
    with pytest.raises(ValueError, match="不一致"):
        await tool.run_for_call(
            run_id=1,
            tool_call_id="changed",
            directives=[{**plan, "cancel_if_price_below": 8}],
        )
    with pytest.raises(ValueError, match="失效下界"):
        await tool.run_for_call(
            run_id=1,
            tool_call_id="conflict",
            directives=[{**plan, "cancel_if_price_below": 11}],
        )
    # A deliberate cancellation remains possible after the buy.
    await tool.run_for_call(
        run_id=1, tool_call_id="cancel-plan", directives=[{**plan, "action": "cancel"}]
    )


def test_handoff_does_not_pass_contradictory_existing_buy_plan():
    plan = OrderDirective(
        order_id="test",
        symbol="600000",
        stock_name="合成股票",
        note="保留",
        action=DirectiveAction.HOLD,
        issued_by_run_id=1,
        cancel_if_price_below=11,
    )
    result = check_handoff(1, [order(order_price=10)], (plan,))
    assert result.status == "incomplete"
    assert "失效下界" in result.issues[0]


def test_quote_uses_ttm_and_pb_never_dynamic_pe_or_unlabelled_fallback():
    raw = {
        "data": {
            "f57": "600000",
            "f58": "合成股票",
            "f43": 20,
            "f164": 10,
            "f162": 1,
            "f167": 1.2,
        }
    }
    result = normalize_quotes(
        "eastmoney", raw, QuoteSnapshotRequest(("600000",), detail="full")
    )
    row = result.data["quotes"][0]
    assert row["pe_ttm"] == 10
    assert row["pb"] == 1.2


def test_tencent_rolling_pe_and_timestamp_survive_normalization():
    fields = [""] * 54
    for index, value in {
        1: "合成股票",
        2: "600000",
        3: "20",
        30: "20261023101500",
        39: "10",
        46: "1.2",
        52: "999",
        53: "888",
    }.items():
        fields[index] = value
    raw = _parse_quotes('v_sh600000="' + "~".join(fields) + '";')
    result = normalize_quotes(
        "tencent", {"quotes": raw}, QuoteSnapshotRequest(("600000",), detail="full")
    )
    quote = result.data["quotes"][0]
    assert quote["pe_ttm"] == 10
    assert quote["pb"] == 1.2
    assert datetime.fromisoformat(quote["market_time"]) == datetime(
        2026, 10, 23, 2, 15, tzinfo=UTC
    )
    assert normalize_datetime("20261323101500") is None
