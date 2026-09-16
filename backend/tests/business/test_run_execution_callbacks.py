"""Trace slimming and trade-notification behaviour of run callbacks."""

from types import SimpleNamespace

import pytest

from backend.business.notifications import (
    NotificationEvent,
    NotificationEventKind,
)
from backend.business.runs import (
    RunEventType,
    StrategyRun,
    StrategySnapshot,
)
from backend.business.runs.callbacks import (
    RunExecutionCallbacks,
    _slim_result_data,
)
from backend.business.runs.runtime import RunRuntimeState
from backend.business.runs.traces import RunTraceRecorder
from backend.business.shared.enums import RunState, TriggerSource


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
        self.published: list[NotificationEvent] = []
        self._fail = fail

    async def publish(self, event: NotificationEvent) -> None:
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
    callbacks = RunExecutionCallbacks(runtime=runtime, notifier=notifier)  # type: ignore[arg-type]
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

    assert [event.kind for event in notifier.published] == [
        NotificationEventKind.ORDER_PLACED
    ]
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
        NotificationEventKind.ORDER_CANCELLED
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


def _live_callbacks(state: RunState) -> tuple[RunExecutionCallbacks, StrategyRun]:
    """Callbacks wired to a real recorder, so the trace can be read back."""

    run = StrategyRun(
        run_id=20260916301,
        trigger_source=TriggerSource.SCHEDULED,
        schedule_id=1,
        snapshot=StrategySnapshot(prompt_version="v1", risk_rules_version="risk-v1"),
        current_state=state,
    )

    async def persist(stored: StrategyRun) -> StrategyRun:
        return stored

    async def publish(_run_id: int, _snapshot: object) -> None:
        return None

    runtime = RunRuntimeState(active_run=run)
    runtime.trace_recorder = RunTraceRecorder(
        run=run, persist_run=persist, publish_snapshot=publish
    )
    return RunExecutionCallbacks(runtime=runtime), run


def _stage(run: StrategyRun, key: str) -> object:
    return next(stage for stage in run.trace.stages if stage.key == key)


@pytest.mark.asyncio
async def test_a_finished_watch_closes_its_stage_and_its_result_step() -> None:
    """盯盘 ends on its own stage — nothing later comes along to close it.

    An analysis has a Summary stage after Run, so a Run stage left open would
    be obvious. A watch's only stage is the last thing that happens, and when
    `on_state_completed` named Run alone it fell through for a watch: stage
    and streamed result step both stayed `running` with no `ended_at`, and the
    detail page kept counting their elapsed time against the wall clock. On
    2026-09-16, 126 of 130 finished watches were recorded that way.
    """

    callbacks, run = _live_callbacks(RunState.WATCH)

    await callbacks.on_state_entered(run.run_id, RunState.WATCH.value)
    await callbacks.on_llm_stream_delta(run.run_id, RunState.WATCH.value, "已核对挂单")
    await callbacks.on_state_completed(
        run.run_id,
        RunState.WATCH.value,
        {"run_content": "# 盯盘记录", "tool_calls_count": 2, "trade_count": 0},
        7_000,
    )

    stage = _stage(run, "watch")
    assert stage.status == "completed"
    assert stage.ended_at is not None
    result = next(step for step in stage.steps if step.step_id == "result")
    assert result.status == "completed"
    assert result.ended_at is not None
    # The title it streamed under, not the analysis's.
    assert result.title == "生成盯盘记录"


@pytest.mark.asyncio
async def test_a_finished_run_stage_still_reads_the_way_it_did() -> None:
    """The generalization above must not rename or restyle 操盘's own step."""

    callbacks, run = _live_callbacks(RunState.RUN)

    await callbacks.on_state_entered(run.run_id, RunState.RUN.value)
    await callbacks.on_llm_stream_delta(run.run_id, RunState.RUN.value, "报告正文")
    await callbacks.on_state_completed(
        run.run_id,
        RunState.RUN.value,
        {"run_content": "# 报告", "tool_calls_count": 9, "trade_count": 1},
        5_000,
    )

    stage = _stage(run, "run")
    assert stage.status == "completed"
    assert stage.summary == "调用工具 9 次，失败 0 次，成功交易 1 次"
    result = next(step for step in stage.steps if step.step_id == "result")
    assert result.title == "生成 Markdown 运行报告"
    assert result.status == "completed"
