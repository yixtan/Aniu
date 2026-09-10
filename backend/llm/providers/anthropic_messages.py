"""Anthropic Messages driver derived from Pi AI.

Upstream inspiration: packages/ai/src/api/anthropic-messages.ts (MIT).
"""

from __future__ import annotations

import re
from typing import Any, cast

import httpx

from backend.llm.abort import AbortSignal, await_with_abort, throw_if_aborted
from backend.llm.contracts import (
    AssistantMessage,
    LLMChatMessage,
    ModelProtocol,
    StopReason,
)
from backend.llm.errors import LLMIntegrationError, provider_error
from backend.llm.events import StreamStarted
from backend.llm.provider_config import ModelAuthMode, ModelProviderConfig
from backend.llm.providers.anthropic_stream import AnthropicStreamAccumulator
from backend.llm.providers.context_payload import provider_context_payload
from backend.llm.providers.extract import _extract_claude_text
from backend.llm.providers.http import (
    _auth_headers,
    _resolved_auth_mode,
    _sdk_endpoint,
    _validate_api_key,
)
from backend.llm.providers.types import (
    DriverRequest,
    EventSink,
    ProviderStreamFailure,
)
from backend.llm.retry import RetryPolicy, retry_provider_request
from backend.llm.thinking import (
    anthropic_adaptive_effort,
    thinking_budget_tokens,
    uses_adaptive_anthropic_thinking,
)


class ClaudeMessagesDriver:
    protocol = ModelProtocol.CLAUDE_API

    # Claude deprecated the sampling knobs from opus-4-7 onward: sending one is
    # a 400, not a warning. opus-4-6 and sonnet-4-6 still take them, so this is
    # a different line than `uses_adaptive_anthropic_thinking` — those two use
    # adaptive thinking *and* accept temperature. Matched by generation because
    # a literal list of names goes stale the day a model ships, and here that
    # reads as a total outage on the new model rather than a degraded run.
    _DEPRECATED_SAMPLING = re.compile(
        r"(?:opus|sonnet|haiku|fable)[-.](?:4[-.][7-9]|[5-9]|\d\d+)"
    )

    @classmethod
    def _deprecates_sampling(cls, model_name: str) -> bool:
        return cls._DEPRECATED_SAMPLING.search(model_name.lower()) is not None

    def _params(self, request: DriverRequest, *, stream: bool) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            **provider_context_payload(
                protocol=request.protocol,
                messages=request.messages,
                tools=request.tools,
                model=request.model,
                provider_config=request.provider_config,
            ),
            "stream": stream,
        }
        if request.thinking_effort is not None:
            if uses_adaptive_anthropic_thinking(request.model):
                params["thinking"] = {"type": "adaptive", "display": "summarized"}
                params["output_config"] = {
                    "effort": anthropic_adaptive_effort(request.thinking_effort)
                }
            else:
                params["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": thinking_budget_tokens(
                        request.thinking_effort,
                        request.max_output_tokens,
                    ),
                }
        elif not self._deprecates_sampling(request.model):
            if request.top_p < 1.0:
                params["top_p"] = request.top_p
            else:
                params["temperature"] = request.temperature
        return params

    async def stream_message(
        self,
        *,
        client: httpx.AsyncClient,
        request: DriverRequest,
        emit: EventSink,
        abort_signal: AbortSignal | None,
        retry_policy: RetryPolicy,
    ) -> AssistantMessage:
        throw_if_aborted(abort_signal)
        await emit(StreamStarted(request.protocol, request.model))
        sdk_base_url = _sdk_endpoint(
            request.protocol,
            request.base_url,
        )
        key = _validate_api_key(request.api_key)
        auth_mode = _resolved_auth_mode(
            request.protocol, request.base_url, request.provider_config
        )
        from anthropic import AsyncAnthropic

        sdk = AsyncAnthropic(
            api_key=key if auth_mode is ModelAuthMode.API_KEY else None,
            auth_token=key if auth_mode is ModelAuthMode.BEARER else None,
            base_url=sdk_base_url,
            http_client=client,
            max_retries=0,
        )
        params = self._params(request, stream=True)

        async def create_stream() -> Any:
            return await cast(Any, sdk.messages).create(**params)

        stream = await retry_provider_request(
            create_stream,
            signal=abort_signal,
            policy=retry_policy,
        )
        accumulator = AnthropicStreamAccumulator(request.protocol, request.model)
        try:
            iterator = stream.__aiter__()
            while True:
                try:
                    event = await await_with_abort(anext(iterator), abort_signal)
                except StopAsyncIteration:
                    break
                await accumulator.accept(event.model_dump(), emit)
            throw_if_aborted(abort_signal)
            return accumulator.finish()
        except Exception as exc:
            aborted = bool(abort_signal is not None and abort_signal.aborted)
            error = exc if aborted else provider_error(exc)
            if isinstance(error, LLMIntegrationError) and accumulator.has_output:
                error.retryable_override = False
            message = await accumulator.failure_message(
                emit,
                stop_reason=StopReason.ABORTED if aborted else StopReason.ERROR,
                error_message=str(error) or type(error).__name__,
            )
            raise ProviderStreamFailure(error, message) from exc
        finally:
            await stream.close()

    def build_text_request(
        self,
        *,
        base_url: str = "",
        api_key: str,
        model: str,
        system_prompt: str | None,
        user_prompt: str,
        temperature: float,
        top_p: float,
        max_output_tokens: int,
        provider_config: ModelProviderConfig = ModelProviderConfig(),
    ) -> dict[str, object]:
        messages: list[LLMChatMessage] = [{"role": "user", "content": user_prompt}]
        if system_prompt and system_prompt.strip():
            messages.insert(0, {"role": "system", "content": system_prompt.strip()})
        request = DriverRequest(
            protocol=self.protocol,
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            tools=[],
            temperature=temperature,
            top_p=top_p,
            max_output_tokens=max_output_tokens,
            provider_config=provider_config,
        )
        return {
            "method": "POST",
            "url": "/messages",
            "headers": {
                **_auth_headers(
                    protocol=self.protocol,
                    base_url=base_url,
                    api_key=api_key,
                    provider_config=provider_config,
                ),
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            "json": self._params(request, stream=False),
        }

    def parse_text_response(self, payload: dict[str, object]) -> str:
        return _extract_claude_text(cast(dict[str, Any], payload))


__all__ = ["ClaudeMessagesDriver"]
