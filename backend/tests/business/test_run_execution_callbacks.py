"""Trace slimming and trade-notification behaviour of run callbacks."""

from types import SimpleNamespace

import pytest

from backend.business.notifications import (
    TradeEventKind,
    TradeNotificationEvent,
)
from backend.business.runs import RunEventType
from backend.business.runs.callbacks import (
    RunExecutionCallbacks,
    _slim_result_data,
)
from backend.business.runs.runtime import RunRuntimeState


def test_slim_result_data_drops_activity_and_content_duplicates() -> None:
    payload = {
        "tool_activity": [{"tool_name": "query_market_data", "content": "巨大输出"}],
        "run_content": "完整 Markdown 运行报告正文",
        "content": "重复报告正文",
        "run_summary": "一句话摘要",
        "tool_calls_count": 9,
        "tool_failure_count": 0,
        "duration_ms": 1234,
        "trade_count": 0,
    }

    assert _slim_result_data(payload) == {
        "run_summary": "一句话摘要",
        "tool_calls_count": 9,
        "tool_failure_count": 0,
        "duration_ms": 1234,
        "trade_count": 0,
    }


def test_slim_result_data_can_drop_summary_for_summary_stage() -> None:
    payload = {"summary": "整份 HTML 总结", "duration_ms": 5}
    assert _slim_result_data(payload, drop_summary=True) == {"duration_ms": 5}
    assert _slim_result_data(payload) == payload


class _RecordingNotifier:
    def __init__(self, fail: bool = False) -> None:
        self.published: list[TradeNotificationEvent] = []
        self._fail = fail

    async def publish(self, event: TradeNotificationEvent) -> None:
        if self._fail:
            raise RuntimeError("dispatcher exploded")
        self.published.append(event)


class _StubRecorder:
    """Minimal recorder surface touched by a completed tool call."""

    async def finish_tool_call_step(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def set_stage_summary(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


def _callbacks(notifier: object) -> tuple[RunExecutionCallbacks, RunRuntimeState]:
    run = SimpleNamespace(run_id=42)
    runtime = RunRuntimeState(active_run=run)  # type: ignore[arg-type]
    runtime.trace_recorder = _StubRecorder()  # type: ignore[assignment]
    callbacks = RunExecutionCallbacks(runtime=runtime, trade_notifier=notifier)  # type: ignore[arg-type]
    return callbacks, runtime


async def _complete_tool_call(
    callbacks: RunExecutionCallbacks,
    *,
    tool_name: str,
    content: object,
    instruction: str = "买入 600519 1700 100",
) -> None:
    await callbacks.on_tool_loop_event(
        42,
        "Run",
        RunEventType.TOOL_CALL_COMPLETED,
        {
            "tool_call_id": "call-1",
            "tool_name": tool_name,
            "status": "ok",
            "arguments": {"instruction": instruction},
            "content": content,
        },
    )


@pytest.mark.asyncio
async def test_completed_trade_call_publishes_an_order_placed_event() -> None:
    notifier = _RecordingNotifier()
    callbacks, _ = _callbacks(notifier)

    await _complete_tool_call(callbacks, tool_name="trade", content={"orderId": "77"})

    assert [event.kind for event in notifier.published] == [TradeEventKind.ORDER_PLACED]
    assert notifier.published[0].order_id == "77"
    assert notifier.published[0].run_id == 42


@pytest.mark.asyncio
async def test_completed_cancel_call_publishes_an_order_cancelled_event() -> None:
    notifier = _RecordingNotifier()
    callbacks, _ = _callbacks(notifier)

    await _complete_tool_call(
        callbacks,
        tool_name="cancel",
        content={"success": True},
        instruction="一键撤单",
    )

    assert [event.kind for event in notifier.published] == [
        TradeEventKind.ORDER_CANCELLED
    ]


@pytest.mark.asyncio
async def test_read_only_tool_calls_never_notify() -> None:
    notifier = _RecordingNotifier()
    callbacks, _ = _callbacks(notifier)

    await _complete_tool_call(
        callbacks, tool_name="query_portfolio", content={"status": "ok"}
    )

    assert notifier.published == []


@pytest.mark.asyncio
async def test_a_failing_notifier_never_breaks_the_run() -> None:
    callbacks, runtime = _callbacks(_RecordingNotifier(fail=True))

    await _complete_tool_call(callbacks, tool_name="trade", content={"orderId": "77"})

    # The tool call still counted toward the stage; only the push was lost.
    assert runtime.stage_tool_calls == 1
