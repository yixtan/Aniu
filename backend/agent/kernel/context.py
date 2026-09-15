"""Runtime context used by the generic agent loop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from backend.agent.kernel.repeat_guard import RepeatedFailureLedger
from backend.agent.kernel.runtime_config import LlmRuntimeConfig
from backend.llm import AbortSignal, LLMClientPort

AgentEventSink = Callable[[str, dict[str, object]], Awaitable[None]]
AgentStreamSink = Callable[[str, str], Awaitable[None]]
ToolAuthorization = Callable[[object, dict[str, object]], str | None]


@dataclass(slots=True)
class AgentContext:
    """Dependencies frozen for one generic AgentHarness prompt run."""

    runtime: LlmRuntimeConfig | None
    llm_client: LLMClientPort
    system_prompt: str = ""
    tool_registry: object | None = None
    abort_signal: AbortSignal | None = None
    event_sink: AgentEventSink | None = None
    stream_sink: AgentStreamSink | None = None
    tool_authorizer: ToolAuthorization | None = None
    label: str = "agent"
    repeated_failures: RepeatedFailureLedger = field(
        default_factory=RepeatedFailureLedger
    )
    """Per-run tally of identical calls that failed; see `repeat_guard`.

    The one piece of state on a context that otherwise holds only frozen
    dependencies. It belongs here because its lifetime is exactly this
    context's: a tally that outlived the turn would refuse a call the next
    turn had every right to make.
    """


__all__ = [
    "AgentContext",
    "AgentEventSink",
    "AgentStreamSink",
    "ToolAuthorization",
]
