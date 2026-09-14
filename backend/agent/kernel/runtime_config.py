"""Typed runtime configuration for one agent / LLM call path."""

from __future__ import annotations

from dataclasses import dataclass

from backend.llm import ModelProtocol, ModelProviderConfig, ThinkingEffort

DEFAULT_MAX_PARALLEL_TOOL_CALLS = 10


@dataclass(frozen=True, slots=True)
class LlmRuntimeConfig:
    """Resolved model endpoint used by a agent turn."""

    protocol: ModelProtocol
    base_url: str
    api_key: str
    model: str
    provider_config: ModelProviderConfig = ModelProviderConfig()
    temperature: float = 0.0
    randomness: float = 1.0
    thinking_effort: ThinkingEffort | None = None
    channel_profile_id: int | None = None
    """Which configured channel this endpoint came from.

    Carried so a caller can record who was asked without resolving the model a
    second time. ``None`` when a runtime was assembled directly rather than
    from a stored channel, as tests do.
    """
    max_parallel_tool_calls: int = DEFAULT_MAX_PARALLEL_TOOL_CALLS
    context_window_tokens: int = 128_000
    max_output_tokens: int = 32_768
