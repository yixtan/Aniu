"""What the watch stage sends, and what it does when there is no plan."""

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

    Worth knowing apart from the run never having mentioned it, because the
    failure this whole mechanism exists for is the silent kind.
    """

    runner = RecordingRunner()
    directive = _directive(rejected_reason="cancel_if_price_above 不是数字")

    await WatchStage().execute(_context(), runner, directives=(directive,))

    assert "rejected_reason" in runner.prompts[0]


@pytest.mark.asyncio
async def test_with_no_plan_the_stage_says_so_and_sends_no_order_plan() -> None:
    """Look but do not touch. The authorizer enforces it; this explains it."""

    runner = RecordingRunner()

    await WatchStage().execute(_context(), runner, directives=())

    sent = runner.prompts[0]
    assert "order_plan" not in sent
    assert "不得进行任何撤单或下单操作" in sent


@pytest.mark.asyncio
async def test_an_empty_record_is_refused() -> None:
    """Silence is the failure mode, so an empty record is not a pass."""

    runner = RecordingRunner(content="   ")

    with pytest.raises(ValueError, match="watch record"):
        await WatchStage().execute(_context(), runner, directives=(_directive(),))
