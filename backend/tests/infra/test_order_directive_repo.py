"""The directives in force must survive the round trip, or authority is lost."""

from __future__ import annotations

from datetime import time

import pytest

from backend.business.order_directives import (
    DirectiveAction,
    OrderDirectiveService,
    RepricePlan,
    parse_directive,
)
from backend.infra.repositories import OrderDirectiveRepository

RUN_ID = 20260912101
LATER_RUN_ID = 20260912102


def _entry(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "order_id": "262534700000036039",
        "symbol": "601869",
        "stock_name": "长飞光纤",
        "action": "hold",
        "note": "筹码分散，但 435 以下仍有赔率",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_every_condition_comes_back_exactly_as_it_went_in(session) -> None:
    repository = OrderDirectiveRepository(session)
    await repository.replace_all(
        run_id=RUN_ID,
        directives=[
            parse_directive(
                _entry(
                    cancel_if_price_above=445.0,
                    cancel_if_price_below=430.0,
                    cancel_if_unfilled_after="14:30",
                ),
                run_id=RUN_ID,
            ),
            parse_directive(
                _entry(
                    order_id="B",
                    action="reprice",
                    reprice={
                        "when_price_above": 442.0,
                        "new_price": 445.0,
                        "max_times": 2,
                    },
                ),
                run_id=RUN_ID,
            ),
        ],
    )
    await session.commit()

    held, repriced = await repository.list_current()

    assert held.action is DirectiveAction.HOLD
    assert held.cancel_if_price_above == 445.0
    assert held.cancel_if_price_below == 430.0
    assert held.cancel_if_unfilled_after == time(14, 30)
    assert held.issued_by_run_id == RUN_ID
    assert repriced.action is DirectiveAction.REPRICE
    assert repriced.reprice == RepricePlan(
        new_price=445.0, max_times=2, when_price_above=442.0
    )


@pytest.mark.asyncio
async def test_a_later_run_replaces_the_list_rather_than_adding_to_it(session) -> None:
    """An order the new run did not mention must not keep the old authority."""

    repository = OrderDirectiveRepository(session)
    await repository.replace_all(
        run_id=RUN_ID,
        directives=[
            parse_directive(_entry(order_id="A", action="cancel"), run_id=RUN_ID),
            parse_directive(_entry(order_id="B"), run_id=RUN_ID),
        ],
    )
    await session.commit()

    await repository.replace_all(
        run_id=LATER_RUN_ID,
        directives=[parse_directive(_entry(order_id="B"), run_id=LATER_RUN_ID)],
    )
    await session.commit()

    current = await repository.list_current()
    assert [item.order_id for item in current] == ["B"]
    assert current[0].issued_by_run_id == LATER_RUN_ID


@pytest.mark.asyncio
async def test_a_downgraded_directive_keeps_saying_why(session) -> None:
    """Otherwise a rejected entry is indistinguishable from one never written."""

    repository = OrderDirectiveRepository(session)
    result = await OrderDirectiveService(repository).record(
        [_entry(action="撤掉")], run_id=RUN_ID
    )
    await session.commit()

    assert result.accepted == 0
    assert result.downgraded == 1
    stored = (await repository.list_current())[0]
    assert stored.action is DirectiveAction.HOLD
    assert "撤掉" in stored.rejected_reason


@pytest.mark.asyncio
async def test_a_reprice_is_counted_against_its_budget(session) -> None:
    repository = OrderDirectiveRepository(session)
    service = OrderDirectiveService(repository)
    await service.record(
        [
            _entry(
                action="reprice",
                reprice={"when_price_above": 442.0, "new_price": 445.0},
            )
        ],
        run_id=RUN_ID,
    )
    await session.commit()

    assert (await service.current())[0].reprices_left == 1

    await service.note_repriced("262534700000036039")
    await session.commit()

    assert (await service.current())[0].reprices_left == 0


@pytest.mark.asyncio
async def test_uncovered_orders_are_reported_rather_than_inferred(session) -> None:
    """Silence is the failure mode, so an unaddressed order gets counted."""

    service = OrderDirectiveService(OrderDirectiveRepository(session))
    await service.record([_entry(order_id="A")], run_id=RUN_ID)
    await session.commit()

    assert await service.uncovered(["A", "B", "C"]) == ["B", "C"]


@pytest.mark.asyncio
async def test_an_empty_plan_leaves_nothing_standing(session) -> None:
    """No plan means no authority to act — the watcher looks and does nothing."""

    service = OrderDirectiveService(OrderDirectiveRepository(session))
    await service.record([_entry(order_id="A", action="cancel")], run_id=RUN_ID)
    await session.commit()

    await service.record([], run_id=LATER_RUN_ID)
    await session.commit()

    assert await service.current() == []
    assert await service.uncovered(["A"]) == ["A"]


@pytest.mark.asyncio
async def test_a_replaced_plan_is_archived_rather_than_discarded(session) -> None:
    """The failure of 2026-09-15, in the layer that made it possible.

    A run wrote "above 190 and the pullback thesis is dead", a watch cancelled
    on it, and the next analysis re-placed the same order larger — because by
    then the condition existed nowhere it could read.
    """

    repository = OrderDirectiveRepository(session)
    await repository.replace_all(
        run_id=RUN_ID,
        directives=[
            parse_directive(
                _entry(
                    order_id="262574600000039111",
                    symbol="688008",
                    stock_name="澜起科技",
                    note="185 回踩承接；涨破 190 则回踩逻辑失效",
                    cancel_if_price_above=190,
                ),
                run_id=RUN_ID,
            )
        ],
    )
    await repository.replace_all(run_id=LATER_RUN_ID, directives=[])

    assert await repository.list_current() == []
    superseded = await repository.list_previous()
    assert [item.order_id for item in superseded] == ["262574600000039111"]
    assert superseded[0].cancel_if_price_above == 190
    assert superseded[0].issued_by_run_id == RUN_ID
    assert "涨破 190" in superseded[0].note


@pytest.mark.asyncio
async def test_only_the_generation_just_replaced_comes_back(session) -> None:
    """One step of memory, not an archive to read past."""

    repository = OrderDirectiveRepository(session)
    for index, run_id in enumerate((RUN_ID, LATER_RUN_ID, LATER_RUN_ID + 1)):
        await repository.replace_all(
            run_id=run_id,
            directives=[
                parse_directive(
                    _entry(order_id=f"order-{index}"), run_id=run_id
                )
            ],
        )

    assert [item.order_id for item in await repository.list_current()] == ["order-2"]
    assert [item.order_id for item in await repository.list_previous()] == ["order-1"]


@pytest.mark.asyncio
async def test_nothing_archived_yet_reads_as_nothing(session) -> None:
    repository = OrderDirectiveRepository(session)
    assert await repository.list_previous() == []

    service = OrderDirectiveService(repository)
    assert await service.previous() == []
