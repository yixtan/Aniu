"""Synthetic orders exercise policy, crash recovery and real durable claims."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock, Mock

import pytest
from sqlalchemy import select

from backend.business.runs.abort import RunAbortSignal
from backend.business.shared import RunAbortError
from backend.infra.db.models import ToolInvocationModel, ValuationCancellationModel
from backend.infra.integrations.mx_agent_tools import CancelTool, TradeTool
from backend.infra.integrations.valuation_cancellation import (
    ValuationCancellationService,
)
from backend.infra.repositories.valuation_cancellation_repo import (
    refuse_managed_cancellation,
)
from backend.tests.infra.buy_fixtures import market_service
from backend.tests.infra.test_exposure_execution_guard import (
    clients,
    declare,
    order,
    seed,
)

NOW = datetime(2026, 10, 29, 6, 30, tzinfo=UTC)
OID = "100000001"


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(
        "backend.infra.integrations.valuation_cancellation.utc_now", lambda: NOW
    )
    monkeypatch.setattr(
        "backend.infra.integrations.exposure_trade_guard.utc_now", lambda: NOW
    )


async def setup(factory, *, status="PENDING", filled=0, market=None):
    await seed(factory, 1, started=NOW.isoformat())
    portfolio, trading = clients(
        orders=[
            order(order_id=OID, submitted_at=NOW, status=status, filled_quantity=filled)
        ]
    )
    trading.cancel = AsyncMock(return_value={"code": 200, "data": {"orderId": OID}})
    service = ValuationCancellationService(
        factory, portfolio, trading, market or market_service(pe=30)
    )
    return service, portfolio, trading


async def record(factory):
    async with factory() as session:
        return await session.get(ValuationCancellationModel, OID)


async def test_fresh_pass_keeps_order_without_write_or_pretrade_approval(
    session_factory,
):
    service, _, trading = await setup(session_factory, market=market_service())
    report = await service.cycle(1, None, lambda: True)
    assert report.items == ()
    assert await record(session_factory) is None
    trading.cancel.assert_not_awaited()


async def test_fail_is_latched_and_committed_before_cancel_and_not_replayed(
    session_factory,
):
    service, portfolio, trading = await setup(session_factory)

    async def send(instruction, *, before_send):
        before_send()
        row = await record(session_factory)
        assert row.state == "SUBMITTING"
        assert row.cause == "VALUATION_FAILED"
        async with session_factory() as session:
            call = await session.scalar(select(ToolInvocationModel))
            assert call.status == "STARTED" and call.tool_name == "cancel"
        return {"code": 200}

    trading.cancel.side_effect = send
    first = await service.cycle(1, None, lambda: True)
    assert first.items[0].state == "SUBMITTED"
    # A later passing source cannot silently restore the invalidated order.
    service.market_data = market_service()
    again = await service.cycle(1, None, lambda: True)
    assert again.items[0].state == "SUBMITTED"
    trading.cancel.assert_awaited_once_with(f"撤单 {OID} 600000", before_send=ANY)
    portfolio.get_orders.return_value = [
        replace(portfolio.get_orders.return_value[0], status="CANCELLED")
    ]
    confirmed = await service.cycle(1, None, lambda: True)
    assert confirmed.items[0].state == "CANCELLED"
    assert "账户已确认撤销" in confirmed.markdown()


@pytest.mark.parametrize("filled,status", [(0, "PENDING"), (30, "PARTIAL")])
async def test_cancel_only_remaining_and_preserve_fills(
    session_factory, filled, status
):
    service, portfolio, trading = await setup(
        session_factory, filled=filled, status=status
    )

    async def send(instruction, *, before_send):
        before_send()
        portfolio.get_orders.return_value = [
            replace(
                portfolio.get_orders.return_value[0],
                status="PARTIAL_CANCELLED" if filled else "CANCELLED",
            )
        ]
        return {"code": 200}

    trading.cancel.side_effect = send
    report = await service.cycle(1, None, lambda: True)
    assert report.items[0].filled_quantity == filled
    assert report.items[0].state == ("PARTIAL_CANCELLED" if filled else "CANCELLED")
    trading.trade.assert_not_awaited()


@pytest.mark.parametrize(
    "status", ["FILLED", "CANCELLED", "REJECTED", "PARTIAL_CANCELLED"]
)
async def test_terminal_order_is_never_audited_or_cancelled(session_factory, status):
    service, _, trading = await setup(
        session_factory, status=status, filled=100 if status == "FILLED" else 0
    )
    report = await service.cycle(1, None, lambda: True)
    assert report.items == ()
    service.market_data.execute_fresh.assert_not_awaited()
    trading.cancel.assert_not_awaited()


async def test_two_missing_reads_trigger_protection_not_false_valuation_failure(
    session_factory,
):
    market = Mock(execute_fresh=AsyncMock(side_effect=RuntimeError("synthetic outage")))
    service, _, trading = await setup(session_factory, market=market)
    report = await service.cycle(1, None, lambda: True)
    assert market.execute_fresh.await_count == 4  # Quote and valuation, twice.
    assert report.items[0].cause == "UNVERIFIABLE"
    assert "无法核验" in report.markdown()
    trading.cancel.assert_awaited_once()


async def test_stale_failure_is_protective_not_fresh_failure(session_factory):
    market = market_service(pe=30)
    original = market.execute_fresh.side_effect

    async def stale(request, abort=None):
        result = await original(request, abort)
        for quote in result.get("data", {}).get("quotes", []):
            quote["market_time"] = "2026-01-01T01:00:00+00:00"
        return result

    market.execute_fresh.side_effect = stale
    service, _, _ = await setup(session_factory, market=market)
    report = await service.cycle(1, None, lambda: True)
    assert report.items[0].cause == "UNVERIFIABLE"


async def test_failed_first_read_then_fresh_pass_does_not_cancel(session_factory):
    market = market_service()
    original = market.execute_fresh.side_effect
    calls = 0

    async def recovering(request, abort=None):
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise RuntimeError("temporary")
        return await original(request, abort)

    market.execute_fresh.side_effect = recovering
    service, _, trading = await setup(session_factory, market=market)
    assert (await service.cycle(1, None, lambda: True)).items == ()
    assert calls == 4
    trading.cancel.assert_not_awaited()


async def test_closed_window_persists_until_later_permitted_cycle(session_factory):
    service, _, trading = await setup(session_factory)
    report = await service.cycle(1, None, lambda: False)
    assert report.items[0].state == "REQUIRED"
    trading.cancel.assert_not_awaited()
    await service.cycle(1, None, lambda: True)
    trading.cancel.assert_awaited_once()


async def test_final_http_guard_can_refuse_without_poisoning_future_attempt(
    session_factory,
):
    service, _, trading = await setup(session_factory)
    allowed = True
    wire = []

    async def send(instruction, *, before_send):
        nonlocal allowed
        allowed = False
        before_send()
        wire.append(instruction)

    trading.cancel.side_effect = send
    report = await service.cycle(1, None, lambda: allowed)
    assert report.items[0].state == "REQUIRED" and wire == []
    allowed = True
    trading.cancel.side_effect = None
    await service.cycle(1, None, lambda: True)
    assert trading.cancel.await_count == 2  # First invocation sent no HTTP.
    assert (await record(session_factory)).attempts == 2


@pytest.mark.parametrize(
    "failure", [TimeoutError("unknown acceptance"), RuntimeError("network")]
)
async def test_unknown_write_is_not_resent_after_restart(session_factory, failure):
    service, portfolio, trading = await setup(session_factory)
    trading.cancel.side_effect = failure
    first = await service.cycle(1, None, lambda: True)
    assert first.items[0].state == "UNKNOWN"
    restarted = ValuationCancellationService(
        session_factory, portfolio, trading, service.market_data
    )
    await restarted.cycle(1, None, lambda: True)
    trading.cancel.assert_awaited_once()


async def test_crash_after_durable_claim_is_query_only(session_factory):
    service, _, trading = await setup(session_factory)
    await service.cycle(1, None, lambda: False)
    from backend.infra.repositories.valuation_cancellation_repo import (
        ValuationCancellationRepository,
    )

    async with session_factory() as session:
        await ValuationCancellationRepository(session).claim(OID, 1)
        await session.commit()
    report = await service.cycle(1, None, lambda: True)
    assert report.items[0].state == "UNKNOWN"
    trading.cancel.assert_not_awaited()


async def test_concurrent_cycles_send_at_most_once(session_factory):
    service, _, trading = await setup(session_factory)
    await asyncio.gather(
        service.cycle(1, None, lambda: True), service.cycle(1, None, lambda: True)
    )
    trading.cancel.assert_awaited_once()


async def test_missing_confirmation_never_means_cancelled(session_factory):
    service, portfolio, trading = await setup(session_factory)

    async def send(instruction, *, before_send):
        before_send()
        portfolio.get_orders.return_value = []
        return {"code": 200}

    trading.cancel.side_effect = send
    report = await service.cycle(1, None, lambda: True)
    assert report.items[0].state == "UNKNOWN"
    assert "账户已确认撤销" not in report.markdown()


async def test_changed_identity_is_never_touched_and_read_failure_is_not_terminal(
    session_factory,
):
    service, portfolio, trading = await setup(session_factory)
    await service.cycle(1, None, lambda: False)
    portfolio.get_orders.return_value = [
        replace(portfolio.get_orders.return_value[0], symbol="600001")
    ]
    await service.cycle(1, None, lambda: True)
    trading.cancel.assert_not_awaited()
    portfolio.get_orders.side_effect = RuntimeError("read down")
    report = await service.cycle(1, None, lambda: True)
    assert report.issues and (await record(session_factory)).state == "REQUIRED"


async def test_model_cannot_cancel_or_rebuy_managed_order(session_factory):
    service, portfolio, trading = await setup(session_factory)
    await service.cycle(1, None, lambda: False)
    tool = CancelTool(trading, portfolio, session_factory=session_factory)
    for instruction in (f"撤单 {OID} 600000", "一键撤单"):
        with pytest.raises(ValueError, match="系统估值撤单"):
            await tool.run(instruction)
    await declare(session_factory, 1, 8)
    trade = TradeTool(trading, portfolio, session_factory=session_factory)
    with pytest.raises(ValueError, match="系统估值撤单"):
        await trade.run_for_call(
            run_id=1, tool_call_id="buy", instruction="买入 600000 20 100"
        )
    trading.trade.assert_not_awaited()
    trading.cancel.assert_not_awaited()


async def test_confirmed_close_still_blocks_reentry_in_origin_run_only(session_factory):
    service, portfolio, _ = await setup(session_factory)
    await service.cycle(1, None, lambda: False)
    portfolio.get_orders.return_value = [
        replace(portfolio.get_orders.return_value[0], status="CANCELLED")
    ]
    await service.cycle(1, None, lambda: True)
    async with session_factory() as session:
        with pytest.raises(ValueError):
            await refuse_managed_cancellation(session, symbol="600000", run_id=1)
        await refuse_managed_cancellation(session, symbol="600000", run_id=2)


async def test_aborted_cycle_sends_nothing(session_factory):
    service, _, trading = await setup(session_factory)
    signal = RunAbortSignal(1)
    signal.abort("test")
    with pytest.raises(RunAbortError):
        await service.cycle(1, signal, lambda: True)
    trading.cancel.assert_not_awaited()


@pytest.mark.parametrize("submitted", [None, datetime(2026, 10, 28, 6, 30, tzinfo=UTC)])
async def test_missing_or_old_submission_time_is_not_guessed(
    session_factory, submitted
):
    service, portfolio, trading = await setup(session_factory)
    portfolio.get_orders.return_value = [
        replace(portfolio.get_orders.return_value[0], submitted_at=submitted)
    ]
    report = await service.cycle(1, None, lambda: True)
    assert report.items[0].state == "REQUIRED"
    trading.cancel.assert_not_awaited()


async def test_fills_during_preflight_close_order_without_cancelling(session_factory):
    service, portfolio, trading = await setup(session_factory)
    pending = portfolio.get_orders.return_value[0]
    portfolio.get_orders.side_effect = [
        [pending],
        [replace(pending, status="FILLED", filled_quantity=100)],
    ]
    report = await service.cycle(1, None, lambda: True)
    assert report.items[0].state == "CLOSED"
    trading.cancel.assert_not_awaited()


async def test_duplicate_order_identity_does_not_authorize_cancellation(
    session_factory,
):
    service, portfolio, trading = await setup(session_factory)
    portfolio.get_orders.return_value *= 2
    report = await service.cycle(1, None, lambda: True)
    assert report.issues and not report.items
    trading.cancel.assert_not_awaited()


async def test_cancel_run_cannot_buy_back_after_confirmation(session_factory):
    service, portfolio, trading = await setup(session_factory)
    await service.cycle(1, None, lambda: False)
    await seed(session_factory, 2, started=NOW.isoformat())

    async def send(instruction, *, before_send):
        before_send()
        portfolio.get_orders.return_value = [
            replace(portfolio.get_orders.return_value[0], status="CANCELLED")
        ]
        return {"code": 200}

    trading.cancel.side_effect = send
    await service.cycle(2, None, lambda: True)
    async with session_factory() as session:
        with pytest.raises(ValueError):
            await refuse_managed_cancellation(session, symbol="600000", run_id=2)
        await refuse_managed_cancellation(session, symbol="600000", run_id=3)


async def test_active_state_with_no_remaining_quantity_is_not_cancelled(
    session_factory,
):
    service, portfolio, trading = await setup(session_factory)
    pending = portfolio.get_orders.return_value[0]
    portfolio.get_orders.side_effect = [
        [pending],
        [replace(pending, filled_quantity=100)],
    ]
    report = await service.cycle(1, None, lambda: True)
    assert report.items[0].state == "UNKNOWN"
    trading.cancel.assert_not_awaited()


@pytest.mark.parametrize("status", ["STARTED", "FAILED", "COMPLETED"])
@pytest.mark.parametrize("instruction", [f"撤单 {OID} 600000", "一键撤单"])
async def test_legacy_cancel_request_is_adopted_never_resent(
    session_factory, status, instruction
):
    import json

    service, _, trading = await setup(session_factory)
    async with session_factory() as session:
        session.add(
            ToolInvocationModel(
                run_id=1,
                tool_call_id="legacy-cancel",
                tool_name="cancel",
                arguments_json=json.dumps({"instruction": instruction}),
                status=status,
                result_json=json.dumps({"code": 200})
                if status == "COMPLETED"
                else None,
                created_at=NOW.isoformat(),
                updated_at=NOW.isoformat(),
            )
        )
        await session.commit()
    report = await service.cycle(1, None, lambda: True)
    assert report.items[0].state == (
        "SUBMITTED" if status == "COMPLETED" else "UNKNOWN"
    )
    assert (await record(session_factory)).request_call_id == "legacy-cancel"
    await service.cycle(1, None, lambda: True)
    trading.cancel.assert_not_awaited()


async def test_old_blanket_cancel_does_not_affect_later_order(session_factory):
    import json

    service, _, trading = await setup(session_factory)
    async with session_factory() as session:
        session.add(
            ToolInvocationModel(
                run_id=1,
                tool_call_id="old-cancel",
                tool_name="cancel",
                arguments_json=json.dumps({"instruction": "一键撤单"}),
                status="STARTED",
                created_at="2026-10-28T06:30:00+00:00",
                updated_at=NOW.isoformat(),
            )
        )
        await session.commit()
    await service.cycle(1, None, lambda: True)
    trading.cancel.assert_awaited_once()
