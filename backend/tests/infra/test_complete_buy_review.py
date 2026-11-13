"""Complete evidence and cancellation causes gate writes, not only prompts."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from backend.infra.db.models import MemoryItemModel
from backend.infra.integrations.buy_decision_review import check_reentry
from backend.infra.integrations.buy_preflight import PrepareBuyTool
from backend.infra.integrations.buy_research_data import (
    previous_sessions,
    research_data,
)
from backend.infra.integrations.buy_research_tool import ReviewBuyTool
from backend.infra.integrations.mx_agent_tools import CancelTool, TradeTool
from backend.stock_api.public.contracts import (
    FinancialsRequest,
    StockMoneyFlowHistoryRequest,
)
from backend.tests.infra.buy_fixtures import (
    approve,
    decision_for,
    market_service,
    record,
)
from backend.tests.infra.test_exposure_execution_guard import (
    clients,
    declare,
    order,
    seed,
    snapshot,
)

BUY = "买入 600000 10 100"


@pytest.mark.parametrize(
    "case",
    [
        "negative",
        "missing",
        "duplicate",
        "today",
        "mixed-source",
        "stale",
        "missing-profit",
        "old-quarter",
        "nonconsecutive",
    ],
)
async def test_incomplete_research_fails_closed(case):
    service = market_service()
    original = service.execute_fresh.side_effect

    async def change(request, abort=None):
        result = await original(request, abort)
        if isinstance(request, StockMoneyFlowHistoryRequest):
            items = result["data"]["items"]
            if case == "negative":
                items[0]["main_net_inflow"] = -1
            if case == "missing":
                items.pop()
            if case == "duplicate":
                items.append(items[0])
            if case == "today":
                items[0]["time"] = datetime.now(UTC).date().isoformat()
            if case == "mixed-source":
                result["meta"]["source"] = "unknown"
            if case == "stale":
                result["meta"]["fetched_at"] = (
                    datetime.now(UTC) - timedelta(minutes=2)
                ).isoformat()
        if isinstance(request, FinancialsRequest):
            if case == "missing-profit":
                result["data"]["items"][0]["net_profit"] = None
            if case == "old-quarter":
                result["data"]["items"][0]["report_period"] = "2020-03-31"
            if case == "nonconsecutive":
                result["data"]["items"][1] = result["data"]["items"][0]
        return result

    service.execute_fresh.side_effect = change
    with pytest.raises(ValueError):
        await research_data(service, "600000", datetime.now(UTC), None)


def test_flow_sessions_skip_weekends():
    assert previous_sessions(date(2026, 11, 16)) == ["2026-11-13", "2026-11-12"]


@pytest.mark.parametrize(
    "case",
    [
        "missing-decision",
        "omitted-rule",
        "wrong-version",
        "changed-size",
        "changed-memory",
    ],
)
async def test_decision_must_confirm_every_rule_and_final_instruction(
    session_factory, case
):
    await seed(session_factory, 1)
    async with session_factory() as session:
        session.add(
            MemoryItemModel(
                id=1,
                content="【买入规则】买入前必须完整审查经营反证",
                created_task_id=1,
                updated_task_id=1,
            )
        )
        await session.commit()
    p, _ = clients()
    service = market_service()
    evidence = await record(
        session_factory,
        1,
        "research",
        "review_buy",
        {"instruction": BUY},
        ReviewBuyTool(service, p, session_factory).run_for_call,
    )
    decision = decision_for(evidence)
    if case == "missing-decision":
        decision = None
    if case == "omitted-rule":
        decision["memory_reviews"] = []
    if case == "wrong-version":
        decision["memory_reviews"][0]["version"] = 999
    if case == "changed-memory":
        async with session_factory() as session:
            rule = await session.get(MemoryItemModel, 1)
            rule.version += 1
            await session.commit()
    with pytest.raises(ValueError):
        await PrepareBuyTool(service, session_factory, p).run_for_call(
            run_id=1,
            tool_call_id="confirm",
            instruction=BUY if case != "changed-size" else "买入 600000 10 200",
            research_review_call_id="research",
            decision=decision,
        )


@pytest.mark.parametrize(
    "case",
    [
        "same-run",
        "same-day",
        "unknown",
        "watch-price",
        "watch-time",
        "watch-no-conditions",
    ],
)
def test_reentry_cannot_override_unresolved_cancellation(case):
    now = datetime(2026, 11, 13, 6, 55, tzinfo=UTC)
    item = {
        "run_id": 1,
        "requested_at": now.isoformat(),
        "status": "CANCELLED",
        "origin": "run",
        "conditions": {},
    }
    if case == "same-run":
        item["run_id"] = 2
    if case == "unknown":
        item["status"] = "UNKNOWN"
    if case.startswith("watch"):
        item["origin"] = "watch"
        item["conditions"] = (
            {"cancel_if_price_above": 19}
            if case == "watch-price"
            else {"cancel_if_unfilled_after": "14:50"}
            if case == "watch-time"
            else {}
        )
    with pytest.raises(ValueError):
        check_reentry({"cancellations": [item]}, 2, now, {"price": 20})


def test_reentry_allows_new_day_and_cleared_watch_bound():
    now = datetime(2026, 11, 13, 6, 0, tzinfo=UTC)
    item = {
        "run_id": 1,
        "requested_at": (now - timedelta(days=1)).isoformat(),
        "status": "CANCELLED",
        "origin": "legacy",
        "conditions": {},
    }
    check_reentry({"cancellations": [item]}, 2, now, {"price": 20})
    item.update(
        requested_at=now.isoformat(),
        origin="watch",
        conditions={"cancel_if_price_above": 21},
    )
    check_reentry({"cancellations": [item]}, 2, now, {"price": 20})


async def test_cancellation_record_blocks_cross_run_rebuy_before_http(session_factory):
    await seed(session_factory, 1)
    await seed(session_factory, 2)
    p, t = clients(orders=[order(order_price=10)])
    t.cancel.return_value = {"status": "submitted"}
    await record(
        session_factory,
        1,
        "cancel",
        "cancel",
        {"instruction": "撤单 600000 test", "reason": "经营证据恶化，撤销原买入判断"},
        CancelTool(t, p, session_factory=session_factory).run_for_call,
    )
    p.get_orders.return_value = [order(order_price=10, status="CANCELLED")]
    with pytest.raises(ValueError, match="当天"):
        await approve(session_factory, 2, BUY, portfolio=p)
    t.trade.assert_not_awaited()


async def test_changed_facts_stop_write_and_review_cannot_be_spent_twice(
    session_factory,
    monkeypatch,
):
    await seed(session_factory, 1)
    monkeypatch.setattr(
        "backend.infra.integrations.exposure_trade_guard.utc_now",
        lambda: datetime(2026, 10, 23, 6, 40, tzinfo=UTC),
    )
    await declare(session_factory, 1, 20)
    p, t = clients(replace(snapshot(), market_value=0, frozen_cash=0))
    service = await approve(session_factory, 1, BUY, portfolio=p)
    tool = TradeTool(t, p, session_factory=session_factory, market_data=service)
    original = service.execute_fresh.side_effect

    async def changed(request, abort=None):
        result = await original(request, abort)
        if isinstance(request, FinancialsRequest):
            result["data"]["items"][0]["net_profit"] += 1
        return result

    service.execute_fresh.side_effect = changed
    with pytest.raises(ValueError, match="事实变化"):
        await tool.run_for_call(
            run_id=1, tool_call_id="buy", instruction=BUY, buy_review_call_id="review"
        )
    t.trade.assert_not_awaited()
    service.execute_fresh.side_effect = original
    # The same collected evidence cannot mint a second permit.
    async with session_factory() as session:
        from backend.infra.db.models import ToolInvocationModel
        from backend.infra.integrations.buy_review_context import mapping

        evidence = mapping(
            (await session.get(ToolInvocationModel, (1, "review-research"))).result_json
        )
    with pytest.raises(ValueError, match="其他决策"):
        await PrepareBuyTool(service, session_factory, p).run_for_call(
            run_id=1,
            tool_call_id="second-confirm",
            instruction=BUY,
            research_review_call_id="review-research",
            decision=decision_for(evidence),
        )


@pytest.mark.parametrize("extra_warning", [False, True])
async def test_sina_basis_warning_is_not_missing_data(extra_warning):
    from backend.stock_api.public.normalizers.sina_money import HISTORY_BASIS

    service = market_service()
    original = service.execute_fresh.side_effect

    async def sina(request, abort=None):
        result = await original(request, abort)
        if isinstance(request, StockMoneyFlowHistoryRequest):
            result["meta"].update(
                source="sina",
                degraded=True,
                warnings=[HISTORY_BASIS] + (["缺少数据"] if extra_warning else []),
            )
        return result

    service.execute_fresh.side_effect = sina
    if extra_warning:
        with pytest.raises(ValueError, match="降级"):
            await research_data(service, "600000", datetime.now(UTC), None)
    else:
        result = await research_data(service, "600000", datetime.now(UTC), None)
        assert result["flow_source"] == "sina"
        assert len(result["completed_day_flows"]) == 2


async def test_watch_cause_rechecked_against_execution_quote(
    session_factory, monkeypatch
):
    monkeypatch.setattr(
        "backend.infra.integrations.exposure_trade_guard.utc_now",
        lambda: datetime(2026, 10, 23, 6, 40, tzinfo=UTC),
    )
    await seed(session_factory, 1)
    await seed(session_factory, 2)
    await declare(session_factory, 2, 20)
    p, t = clients(
        replace(snapshot(), market_value=0, frozen_cash=0),
        orders=[order(status="CANCELLED")],
    )

    async def cancelled(**kwargs):
        return {
            "cancellation_review": {
                "origin": "watch",
                "reason": "可信价格计划触发撤单",
                "conditions": {"cancel_if_price_above": 21},
            }
        }

    await record(
        session_factory,
        1,
        "cancel",
        "cancel",
        {"instruction": "撤单 600000 test"},
        cancelled,
    )
    service = await approve(session_factory, 2, BUY, portfolio=p)
    original = service.execute_fresh.side_effect

    async def moved(request, abort=None):
        from backend.stock_api.public import QuoteSnapshotRequest

        result = await original(request, abort)
        if isinstance(request, QuoteSnapshotRequest):
            result["data"]["quotes"][0]["price"] = 22
        return result

    service.execute_fresh.side_effect = moved
    with pytest.raises(ValueError, match="原挂单价格"):
        await TradeTool(
            t, p, session_factory=session_factory, market_data=service
        ).run_for_call(
            run_id=2, tool_call_id="buy", instruction=BUY, buy_review_call_id="review"
        )
    t.trade.assert_not_awaited()
