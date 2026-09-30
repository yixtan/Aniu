"""A tool-loop reply that breaks off midway is asked again.

On 2026-09-29 DeepSeek closed the connection 45 seconds into a reply of the
14:15 run: "peer closed connection without sending complete message body
(incomplete chunked read)". The provider marks a stream that fails after the
reply began as not retryable, since what streamed may already be on screen,
so the run failed on its first attempt with two attempts unused. A tool-loop
turn holds its text back until the turn completes and runs no tool before
that, so for it the failure is only a dropped connection.

These tests send real SSE bytes through the provider SDK and cut the body off
the way httpx reports it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx
import pytest

from backend.agent.errors import AgentIntegrationError
from backend.agent.kernel.context import AgentContext
from backend.agent.kernel.llm_runtime import generate_tool_loop_response
from backend.agent.kernel.runtime_config import LlmRuntimeConfig
from backend.infra.observability.log_config import (
    LOG_RECORD_BUILTINS,
    STRUCTURED_FIELDS,
    StructuredJsonFormatter,
)
from backend.llm import (
    Failed,
    LLMClient,
    LLMErrorCode,
    ModelProtocol,
    is_error_retryable,
    is_error_retryable_unshown,
)

_CUT_OFF = (
    "peer closed connection without sending complete message body "
    "(incomplete chunked read)"
)


def _chunk(delta: dict[str, object], finish_reason: str | None = None) -> bytes:
    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "deepseek-flash",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n".encode()


class _CutOffBody(httpx.AsyncByteStream):
    """Sends the start of a reply, then drops the connection."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield _chunk({"reasoning_content": "先看账户，再看委托"})
        raise httpx.RemoteProtocolError(_CUT_OFF)


def _complete_body() -> bytes:
    return (
        _chunk({"reasoning_content": "重新想一遍"})
        + _chunk({"content": "重试后成功"}, finish_reason="stop")
        + b"data: [DONE]\n\n"
    )


def _deepseek(cut_offs: int) -> tuple[LLMClient, list[int]]:
    calls: list[int] = []

    async def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        headers = {"content-type": "text/event-stream"}
        if len(calls) <= cut_offs:
            return httpx.Response(200, stream=_CutOffBody(), headers=headers)
        return httpx.Response(200, content=_complete_body(), headers=headers)

    return LLMClient(transport=httpx.MockTransport(handler)), calls


def _context(client: LLMClient) -> tuple[AgentContext, list[tuple[str, str]]]:
    streamed: list[tuple[str, str]] = []

    async def sink(delta: str, channel: str = "text") -> None:
        streamed.append((channel, delta))

    context = AgentContext(
        runtime=LlmRuntimeConfig(
            protocol=ModelProtocol.OPENAI_CHAT_COMPLETIONS,
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="deepseek-flash",
        ),
        llm_client=client,
        stream_sink=sink,
    )
    return context, streamed


@pytest.mark.asyncio
async def test_the_provider_marks_a_reply_cut_off_midway() -> None:
    client, _ = _deepseek(cut_offs=1)
    events = [
        event
        async for event in client.stream_chat(
            protocol=ModelProtocol.OPENAI_CHAT_COMPLETIONS,
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="deepseek-flash",
            messages=[{"role": "user", "content": "请分析"}],
            temperature=0.0,
        )
    ]

    terminal = events[-1]
    assert isinstance(terminal, Failed)
    error = terminal.error
    assert getattr(error, "error_code") is LLMErrorCode.NETWORK
    assert getattr(error, "interrupted_after_output") is True
    # Still not retryable for a caller that may have shown the partial reply;
    # retryable for one that showed nothing.
    assert is_error_retryable(error) is False  # type: ignore[arg-type]
    assert is_error_retryable_unshown(error) is True  # type: ignore[arg-type]
    await client.aclose()


@pytest.mark.asyncio
async def test_a_tool_loop_reply_cut_off_midway_is_asked_again() -> None:
    client, calls = _deepseek(cut_offs=1)
    context, streamed = _context(client)

    result = await generate_tool_loop_response(
        context,
        label="Run",
        messages=[{"role": "user", "content": "请分析"}],
        tools=[],
        llm_client=client,
    )

    assert result["content"] == "重试后成功"
    assert len(calls) == 2
    thinking = "".join(delta for channel, delta in streamed if channel == "thinking")
    # The trace says why the reasoning starts over.
    assert thinking.index("先看账户") < thinking.index("连接在回复中途断开")
    assert thinking.index("连接在回复中途断开") < thinking.index("重新想一遍")
    await client.aclose()


@pytest.mark.asyncio
async def test_a_reply_cut_off_every_time_still_fails_after_three_attempts() -> None:
    client, calls = _deepseek(cut_offs=3)
    context, _ = _context(client)

    with pytest.raises(AgentIntegrationError, match="incomplete chunked read"):
        await generate_tool_loop_response(
            context,
            label="Run",
            messages=[{"role": "user", "content": "请分析"}],
            tools=[],
            llm_client=client,
        )

    assert len(calls) == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_every_field_a_model_call_logs_reaches_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The log keeps only the fields it lists and drops the rest silently.

    `cut_off_midway` was added to the retry log without being listed, so on
    2026-09-30, the first time a reply was cut off and retried, the log could
    not say that it had been cut off.
    """

    client, _ = _deepseek(cut_offs=1)
    context, _ = _context(client)

    with caplog.at_level(logging.INFO, logger="backend.agent.kernel.llm_runtime"):
        await generate_tool_loop_response(
            context,
            label="Run",
            messages=[{"role": "user", "content": "请分析"}],
            tools=[],
            llm_client=client,
        )

    records = [
        r for r in caplog.records if r.name == "backend.agent.kernel.llm_runtime"
    ]
    assert records
    for record in records:
        extra = set(record.__dict__) - LOG_RECORD_BUILTINS - {"message", "asctime"}
        assert extra <= set(STRUCTURED_FIELDS), (
            record.getMessage(),
            sorted(extra - set(STRUCTURED_FIELDS)),
        )
    retry = next(r for r in records if r.getMessage() == "llm_call_retry")
    logged = json.loads(StructuredJsonFormatter().format(retry))
    assert logged["cut_off_midway"] is True
    await client.aclose()
