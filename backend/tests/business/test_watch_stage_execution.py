"""What the watch stage sends, and when it does not call a model at all."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

import pytest

from backend.business.order_directives import (
    DirectiveAction,
    OrderDirective,
    RepricePlan,
)
from backend.business.runs.execution import RunExecutionContext
from backend.business.runs.run_entity import StrategyRun, StrategySnapshot
from backend.business.runs.stages.watch_stage import WatchStage
from backend.business.shared.enums import TriggerSource


@dataclass
class RecordingRunner:
    prompts: list[str] = field(default_factory=list)
    content: str = "已逐笔核对。"

    async def prompt(self, user_prompt: str, **_: object) -> object:
        self.prompts.append(user_prompt)

        @dataclass(frozen=True)
        class _Result:
            content: str
            tool_activity: tuple[dict[str, object], ...] = ()
            transcript: tuple[dict[str, object], ...] = ()
            total_tokens: int = 0
            cached_tokens: int = 0

        return _Result(content=self.content)


def _context() -> RunExecutionContext:
    snapshot = StrategySnapshot(prompt_version="v1", risk_rules_version="risk-v1")
    return RunExecutionContext(
        run=StrategyRun(
            run_id=20260913301,
            trigger_source=TriggerSource.SCHEDULED,
            schedule_id=1,
            snapshot=snapshot,
        ),
        snapshot=snapshot,
        llm_runtime=object(),
        market_session_is_open=lambda: True,
    )


def _directive(**overrides: object) -> OrderDirective:
    fields: dict[str, object] = {
        "order_id": "262534700000036039",
        "symbol": "601869",
        "stock_name": "长飞光纤",
        "action": DirectiveAction.HOLD,
        "note": "筹码分散，但 435 以下仍有赔率",
        "issued_by_run_id": 20260911106,
    }
    fields.update(overrides)
    return OrderDirective(**fields)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_plan_is_sent_as_data_not_described_in_prose() -> None:
    """A handful of entries, all of which must be read, and all known upfront.

    A tool call would cost a round trip to fetch something the stage already
    has before it starts.
    """

    runner = RecordingRunner()
    directive = _directive(
        cancel_if_price_above=445.0,
        cancel_if_unfilled_after=time(14, 30),
    )

    await WatchStage().execute(_context(), runner, directives=(directive,))

    sent = runner.prompts[0]
    assert "order_plan" in sent
    assert "262534700000036039" in sent
    assert "445" in sent
    assert "14:30" in sent


@pytest.mark.asyncio
async def test_a_reprice_shows_what_is_left_of_its_budget() -> None:
    runner = RecordingRunner()
    directive = _directive(
        action=DirectiveAction.REPRICE,
        reprice=RepricePlan(new_price=445.0, max_times=2, when_price_above=442.0),
        repriced_times=1,
    )

    await WatchStage().execute(_context(), runner, directives=(directive,))

    assert "remaining_times" in runner.prompts[0]


@pytest.mark.asyncio
async def test_a_downgraded_entry_is_surfaced_rather_than_hidden() -> None:
    """The run tried to speak about this order and got the shape wrong.

    That is worth telling apart from the run never mentioning the order,
    because the failure this whole mechanism exists for is the silent kind.
    A downgraded entry is a bare hold, so no model is asked; the record says
    it instead.
    """

    runner = RecordingRunner()
    directive = _directive(rejected_reason="cancel_if_price_above 不是数字")

    report = await WatchStage().execute(_context(), runner, directives=(directive,))

    assert runner.prompts == []
    assert "格式不对" in report.content
    assert "cancel_if_price_above 不是数字" in report.content


@pytest.mark.asyncio
async def test_with_no_plan_no_model_is_called_and_the_record_says_why() -> None:
    """With no plan a watch may not touch anything, so asking a model is waste."""

    runner = RecordingRunner()

    report = await WatchStage().execute(_context(), runner, directives=())

    assert runner.prompts == []
    assert "没有挂单处置计划" in report.content
    assert report.total_tokens == 0


@pytest.mark.asyncio
async def test_bare_holds_call_no_model_and_the_record_lists_each_order() -> None:
    """None of the 384 watches from 2026-09-16 13:00 to 09-29 had anything to
    act on. Each spent about 30,000 tokens saying so."""

    runner = RecordingRunner()
    first = _directive()
    second = _directive(
        order_id="262704700000040391", symbol="600150", stock_name="中国船舶", note=""
    )

    report = await WatchStage().execute(_context(), runner, directives=(first, second))

    assert runner.prompts == []
    assert "2 笔委托都是无条件持有" in report.content
    assert "长飞光纤（601869）委托 262534700000036039：持有。筹码分散" in report.content
    assert "中国船舶（600150）委托 262704700000040391：持有。" in report.content


@pytest.mark.parametrize(
    "overrides",
    [
        {"action": DirectiveAction.CANCEL},
        {"cancel_if_price_above": 445.0},
        {"cancel_if_price_below": 420.0},
        {"cancel_if_unfilled_after": time(14, 30)},
        {
            "action": DirectiveAction.REPRICE,
            "reprice": RepricePlan(
                new_price=445.0, max_times=2, when_price_above=442.0
            ),
            "repriced_times": 1,
        },
    ],
    ids=["cancel", "price-above", "price-below", "time", "reprice-left"],
)
@pytest.mark.asyncio
async def test_anything_the_watch_could_act_on_is_sent_to_the_model(
    overrides: dict[str, object],
) -> None:
    runner = RecordingRunner()
    bare = _directive(order_id="1")

    await WatchStage().execute(
        _context(), runner, directives=(bare, _directive(**overrides))
    )

    assert len(runner.prompts) == 1
    assert "order_plan" in runner.prompts[0]


@pytest.mark.asyncio
async def test_a_reprice_with_no_attempts_left_needs_no_watching() -> None:
    runner = RecordingRunner()
    spent = _directive(
        action=DirectiveAction.REPRICE,
        reprice=RepricePlan(new_price=445.0, max_times=1, when_price_above=442.0),
        repriced_times=1,
    )

    await WatchStage().execute(_context(), runner, directives=(spent,))

    assert runner.prompts == []


@pytest.mark.asyncio
async def test_an_empty_record_is_refused() -> None:
    """Silence is the failure mode, so an empty record is not a pass."""

    runner = RecordingRunner(content="   ")

    with pytest.raises(ValueError, match="watch record"):
        await WatchStage().execute(
            _context(),
            runner,
            directives=(_directive(cancel_if_unfilled_after=time(14, 30)),),
        )
