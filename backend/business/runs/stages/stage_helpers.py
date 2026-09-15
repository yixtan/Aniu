"""Shared helpers for stage prompt preparation and stage events."""

from __future__ import annotations

from backend.business.order_directives import OrderDirective
from backend.business.runs import RunEventType
from backend.business.runs.execution import RunExecutionContext
from backend.business.shared import ServiceConfigurationError

__all__ = [
    "directive_payload",
    "emit_stage_prompt_prepared",
    "emit_tool_loop_event",
    "require_llm_runtime",
]


def directive_payload(
    directive: OrderDirective,
    *,
    include_issuer: bool = False,
) -> dict[str, object]:
    """One directive as a stage sees it.

    One projection for both readers on purpose. A watch is being told what it
    may do; an analysis is being shown what was decided and under what
    condition. If those two drifted into separate shapes, the conditions a
    watch acts on and the conditions an analysis reviews could quietly stop
    being the same conditions.

    `include_issuer` is the one difference: only an analysis cares which run
    said this, because only an analysis is deciding whether to say it again.
    """

    entry: dict[str, object] = {
        "order_id": directive.order_id,
        "symbol": directive.symbol,
        "stock_name": directive.stock_name,
        "action": directive.action.value,
        "note": directive.note,
    }
    if include_issuer:
        entry["issued_by_run_id"] = directive.issued_by_run_id
    if directive.cancel_if_price_above is not None:
        entry["cancel_if_price_above"] = directive.cancel_if_price_above
    if directive.cancel_if_price_below is not None:
        entry["cancel_if_price_below"] = directive.cancel_if_price_below
    if directive.cancel_if_unfilled_after is not None:
        entry["cancel_if_unfilled_after"] = directive.cancel_if_unfilled_after.strftime(
            "%H:%M"
        )
    plan = directive.reprice
    if plan is not None:
        entry["reprice"] = {
            "new_price": plan.new_price,
            "when_price_above": plan.when_price_above,
            "when_price_below": plan.when_price_below,
            "remaining_times": directive.reprices_left,
        }
    if directive.rejected_reason:
        # Surfaced rather than hidden: the run tried to say something about this
        # order and got the shape wrong, which is worth knowing apart from the
        # run never having mentioned it at all.
        entry["rejected_reason"] = directive.rejected_reason
    return entry


def require_llm_runtime(context: RunExecutionContext, *, stage_name: str) -> None:
    """Fail fast when a stage's main LLM runtime is not configured.

    Production stages read the runtime from ``context.llm_runtime``;
    without it, they would emit prompt-prepared events first and only fail inside
    the LLM call. This guard raises before any emit so callers see the
    misconfiguration immediately.
    """

    runtime = context.llm_runtime
    if runtime is None:
        raise ServiceConfigurationError(
            f"{stage_name} stage requires configured llm runtime"
        )


async def emit_stage_prompt_prepared(
    context: RunExecutionContext,
    *,
    stage_name: str,
    phase: str,
    title: str,
    summary: str,
    display_prompt: str,
    payload: dict[str, object] | None = None,
    user_message: str | None = None,
) -> None:
    """Notify the host that a stage prompt is ready for the model."""

    sink = context.stage_prompt_prepared_sink
    if not callable(sink):
        return
    body: dict[str, object] = {
        "stage_name": stage_name,
        "phase": phase,
        "title": title,
        "summary": summary,
        "prompt": display_prompt,
        "prompt_chars": len(display_prompt),
    }
    if payload is not None:
        body["payload"] = payload
    if user_message is not None:
        body["user_message"] = user_message
    await sink(stage_name, body)


async def emit_tool_loop_event(
    context: RunExecutionContext,
    *,
    stage_name: str,
    event_type: RunEventType,
    payload: dict[str, object],
) -> None:
    """Forward a stage-local event to the host tool-loop sink when present."""

    sink = context.tool_loop_event_sink
    if not callable(sink):
        return
    await sink(stage_name, event_type, payload)
