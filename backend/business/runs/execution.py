"""Business execution state and reports for the two-stage workflow."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ClassVar

from backend.business.exposure.models import ExposureCap
from backend.business.open_findings.models import OpenFinding
from backend.business.order_directives import OrderDirective
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
    # Billed by the provider for this stage, or zero when it reported none.
    total_tokens: int = 0
    # The part of that total the provider served from its prompt cache.
    cached_tokens: int = 0

    def as_payload(self) -> dict[str, object]:
        return {
            self.summary_key: _summarize_text(self.content),
            self.content_key: self.content,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
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
    # Billed for rendering the summary. Its own stage and its own spend: the
    # report it reads is the largest single input of the day, so leaving it out
    # understated a run by about a quarter.
    total_tokens: int = 0
    cached_tokens: int = 0

    def as_payload(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
        }


@dataclass(slots=True)
class RunExecutionContext:
    """Mutable context shared by Run and Summary."""

    run: StrategyRun
    snapshot: StrategySnapshot
    run_report: RunReport | None = None
    llm_runtime: object | None = None
    tool_registry: object | None = None
    market_session_is_open: Callable[[], bool] | None = None
    cancellations_accepted: Callable[[], bool] | None = None
    """Whether a cancellation would be accepted right now.

    Narrower than the session: the closing call auction still takes
    orders but refuses withdrawals, so the tool that cancels and the
    tool that trades do not stop at the same minute.
    """
    followed_companies: tuple[tuple[str, str], ...] = ()
    """The operator's watchlist, resolved once before the Run stage.

    Data rather than configuration, so it is read live instead of being frozen
    into the run snapshot: a company followed this morning should be seen by
    this afternoon's run.
    """
    standing_order_plan: tuple[OrderDirective, ...] = ()
    """What the last analysis decided about each resting order, for this one.

    An analysis used to see none of this. It read the account, found no
    pending orders and reasoned from scratch — so on 2026-09-15 one of them
    re-placed, larger, an order a watch had cancelled twenty minutes earlier
    under a condition the previous analysis had itself written ("above 190 and
    the pullback thesis is dead"). Nothing was wrong with either decision on
    its own; the second one simply could not see the first.

    Read live rather than frozen into the snapshot, and for the same reason as
    the watchlist: it is data about the account right now, not configuration.
    """
    previous_order_plan: tuple[OrderDirective, ...] = ()
    """The generation before `standing_order_plan`.

    One generation back, not the whole archive: a run needs to see the arc of
    a decision it is about to revisit, not every statement ever made.
    """
    recent_exposure_caps: tuple[ExposureCap, ...] = ()
    """The caps already declared, newest first, so this run can place its own.

    Read rather than remembered: the number is a state of today, and a state
    kept in long-term memory is what turned 「总敞口约20%封顶」 into a fact
    about the world that six days of rules inherited.

    A series rather than the last row, because the last row is the same trap
    one table over. On 2026-09-22 the run was handed 9/21's declaration with
    its reasoning attached — 「按id150公式1%÷5%=20%」 — and answered 「参数无
    变化」 twice, having checked yesterday's arithmetic instead of doing its
    own. A column of numbers leaves nothing to check and shows the one thing
    a single row cannot: how long this has been standing still.
    """
    open_findings: tuple[OpenFinding, ...] = ()
    """Objections a reviewer raised that nobody has closed yet.

    Read live and capped, like the watchlist, and for the same reason: an
    objection raised this morning belongs in this afternoon's run. Each one
    must be answered — silence on an open finding is what the per-order
    disposition rule already exists to prevent, applied to the same failure
    one level up.
    """
    authorized_order_ids: frozenset[str] | None = None
    """Orders the current plan speaks about, or None outside an order watch.

    The watch decides *when* a stated condition is met; this decides whether
    the order was ever in scope. Keeping the two apart is what makes the
    agreed rule — no plan means look but do not touch — a property of the code
    rather than a sentence in a prompt that a capable model may reason past.

    An empty set is not the same as None: it says a plan was read and it named
    nothing, which forbids every write. None says this is not a watch.
    """
    abort_signal: AbortSignal | None = None
    tool_loop_event_sink: ToolLoopEventSink | None = None
    llm_stream_delta_sink: LlmStreamDeltaSink | None = None
    stage_prompt_prepared_sink: StagePromptPreparedSink | None = None
