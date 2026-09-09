"""Business execution state and reports for the two-stage workflow."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ClassVar

from backend.business.runs import StrategyRun, StrategySnapshot
from backend.business.shared.trading import is_successful_trade_call
from backend.llm import AbortSignal

ToolLoopEventSink = Callable[[str, object, dict[str, object]], Awaitable[None]]
LlmStreamDeltaSink = Callable[..., Awaitable[None]]
StagePromptPreparedSink = Callable[[str, dict[str, object]], Awaitable[None]]


def _summarize_tool_activity(
    tool_activity: tuple[dict[str, object], ...],
) -> dict[str, int]:
    tool_calls_count = 0
    tool_success_count = 0
    tool_failure_count = 0
    for item in tool_activity:
        if str(item.get("record_type") or "") != "tool_loop_failure":
            tool_calls_count += 1
        status = str(item.get("status") or "")
        if status == "ok":
            tool_success_count += 1
        elif status in {"blocked", "error"}:
            tool_failure_count += 1
    return {
        "tool_calls_count": tool_calls_count,
        "tool_success_count": tool_success_count,
        "tool_failure_count": tool_failure_count,
    }


def _summarize_text(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:160]
    return text.strip()[:160]


@dataclass(frozen=True, slots=True)
class RunReport:
    """Markdown result and raw evidence produced by the Run agent."""

    summary_key: ClassVar[str] = "run_summary"
    content_key: ClassVar[str] = "run_content"

    content: str
    tool_activity: tuple[dict[str, object], ...] = ()
    transcript: tuple[dict[str, object], ...] = ()

    def as_payload(self) -> dict[str, object]:
        return {
            self.summary_key: _summarize_text(self.content),
            self.content_key: self.content,
            **_summarize_tool_activity(self.tool_activity),
            "trade_count": sum(
                is_successful_trade_call(
                    tool_name=str(activity.get("tool_name") or ""),
                    status=str(activity.get("status") or ""),
                    content=activity.get("content"),
                )
                for activity in self.tool_activity
            ),
            "tool_activity": [dict(item) for item in self.tool_activity],
        }


@dataclass(frozen=True, slots=True)
class SummaryDraft:
    """Sanitized HTML summary generated from one completed Run report."""

    summary: str

    def as_payload(self) -> dict[str, object]:
        return {"summary": self.summary}


@dataclass(slots=True)
class RunExecutionContext:
    """Mutable context shared by Run and Summary."""

    run: StrategyRun
    snapshot: StrategySnapshot
    run_report: RunReport | None = None
    llm_runtime: object | None = None
    tool_registry: object | None = None
    market_session_is_open: Callable[[], bool] | None = None
    followed_companies: tuple[tuple[str, str], ...] = ()
    """The operator's watchlist, resolved once before the Run stage.

    Data rather than configuration, so it is read live instead of being frozen
    into the run snapshot: a company followed this morning should be seen by
    this afternoon's run.
    """
    abort_signal: AbortSignal | None = None
    tool_loop_event_sink: ToolLoopEventSink | None = None
    llm_stream_delta_sink: LlmStreamDeltaSink | None = None
    stage_prompt_prepared_sink: StagePromptPreparedSink | None = None
