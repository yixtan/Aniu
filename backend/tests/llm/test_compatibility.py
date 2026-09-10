"""OpenAI-compatible and Anthropic request compatibility tests."""

from dataclasses import replace

import pytest

from backend.llm import ModelProtocol, ThinkingEffort
from backend.llm.providers.anthropic_messages import ClaudeMessagesDriver
from backend.llm.providers.openai_chat import OpenAIChatDriver
from backend.llm.providers.types import DriverRequest


def _request(
    *,
    protocol: ModelProtocol,
    base_url: str,
    model: str,
    top_p: float = 1.0,
    thinking_effort: ThinkingEffort | None = None,
    max_output_tokens: int = 1024,
) -> DriverRequest:
    return DriverRequest(
        protocol=protocol,
        base_url=base_url,
        api_key="test-key",
        model=model,
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        temperature=0.3,
        top_p=top_p,
        max_output_tokens=max_output_tokens,
        thinking_effort=thinking_effort,
    )


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5-mini",
        "gpt-5.6-terra",
        "gpt-6-astra",
        "gpt-10-next",
        "o1",
        "o3-mini",
        "o4",
    ],
)
def test_every_reasoning_generation_uses_the_completion_token_field(
    model: str,
) -> None:
    """A new generation must not need this file edited. gpt-6-astra 400'd on
    every run because the rule was a literal list ending at gpt-5."""

    params = OpenAIChatDriver()._params(
        _request(
            protocol=ModelProtocol.OPENAI_CHAT_COMPLETIONS,
            base_url="https://api.openai.com/v1",
            model=model,
        ),
        stream=True,
    )

    assert params["max_completion_tokens"] == 1024
    assert "max_tokens" not in params
    assert "temperature" not in params
    assert "top_p" not in params


@pytest.mark.parametrize("model", ["gpt-4o-mini", "gpt-3.5-turbo", "glm-5.3-flash"])
def test_models_before_the_reasoning_generations_keep_max_tokens(model: str) -> None:
    params = OpenAIChatDriver()._params(
        _request(
            protocol=ModelProtocol.OPENAI_CHAT_COMPLETIONS,
            base_url="https://api.openai.com/v1",
            model=model,
        ),
        stream=True,
    )

    assert params["max_tokens"] == 1024
    assert "max_completion_tokens" not in params
    assert params["temperature"] == 0.3


def test_openai_maps_configured_thinking_effort() -> None:
    params = OpenAIChatDriver()._params(
        _request(
            protocol=ModelProtocol.OPENAI_CHAT_COMPLETIONS,
            base_url="https://api.openai.com/v1",
            model="gpt-5-mini",
            thinking_effort="high",
        ),
        stream=True,
    )

    assert params["reasoning_effort"] == "high"


def test_openai_stream_usage_is_only_requested_from_official_endpoint() -> None:
    driver = OpenAIChatDriver()
    official = driver._params(
        _request(
            protocol=ModelProtocol.OPENAI_CHAT_COMPLETIONS,
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
        ),
        stream=True,
    )
    compatible = driver._params(
        _request(
            protocol=ModelProtocol.OPENAI_CHAT_COMPLETIONS,
            base_url="https://llm.example.com/v1",
            model="gpt-4o-mini",
        ),
        stream=True,
    )

    assert official["stream_options"] == {"include_usage": True}
    assert "stream_options" not in compatible


def test_deepseek_reasoning_is_replayed_for_same_provider() -> None:
    driver = OpenAIChatDriver()
    request = _request(
        protocol=ModelProtocol.OPENAI_CHAT_COMPLETIONS,
        base_url="https://deepseek.example.com/v1",
        model="deepseek-reasoner",
    )
    request = replace(
        request,
        messages=[
            {
                "role": "assistant",
                "content": "answer",
                "reasoning": "reasoning trace",
            }
        ],
    )

    params = driver._params(request, stream=True)

    assert params["messages"][0]["reasoning_content"] == "reasoning trace"


def test_claude_sends_exactly_one_sampling_control() -> None:
    driver = ClaudeMessagesDriver()
    temperature = driver._params(
        _request(
            protocol=ModelProtocol.CLAUDE_API,
            base_url="https://api.anthropic.com/v1",
            model="claude-sonnet-4-6",
        ),
        stream=True,
    )
    top_p = driver._params(
        _request(
            protocol=ModelProtocol.CLAUDE_API,
            base_url="https://api.anthropic.com/v1",
            model="claude-sonnet-4-6",
            top_p=0.8,
        ),
        stream=True,
    )

    assert temperature["temperature"] == 0.3
    assert "top_p" not in temperature
    assert top_p["top_p"] == 0.8
    assert "temperature" not in top_p


@pytest.mark.parametrize(
    "model",
    [
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-sonnet-5",
        "claude-opus-5",
        "claude-fable-5-1",
        "claude-sonnet-6",
    ],
)
def test_claude_omits_sampling_controls_where_they_are_deprecated(model: str) -> None:
    """With no effort configured the request used to fall through to
    `temperature`, which these models refuse outright."""

    params = ClaudeMessagesDriver()._params(
        _request(
            protocol=ModelProtocol.CLAUDE_API,
            base_url="https://api.anthropic.com/v1",
            model=model,
        ),
        stream=True,
    )

    assert "temperature" not in params
    assert "top_p" not in params
    assert "thinking" not in params


@pytest.mark.parametrize(
    "model",
    ["claude-sonnet-4-6", "claude-opus-4-6", "claude-opus-4-5", "claude-haiku-4-5"],
)
def test_claude_keeps_sampling_controls_where_they_still_apply(model: str) -> None:
    """The line is opus-4-7, not "uses adaptive thinking": sonnet-4-6 and
    opus-4-6 do use adaptive thinking and still accept temperature."""

    params = ClaudeMessagesDriver()._params(
        _request(
            protocol=ModelProtocol.CLAUDE_API,
            base_url="https://api.anthropic.com/v1",
            model=model,
        ),
        stream=True,
    )

    assert params["temperature"] == 0.3


def test_claude_maps_adaptive_thinking_effort() -> None:
    params = ClaudeMessagesDriver()._params(
        _request(
            protocol=ModelProtocol.CLAUDE_API,
            base_url="https://api.anthropic.com/v1",
            model="claude-sonnet-4-6",
            thinking_effort="minimal",
            max_output_tokens=10_000,
        ),
        stream=True,
    )

    assert params["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert params["output_config"] == {"effort": "low"}
    assert "temperature" not in params
    assert "top_p" not in params


def test_claude_maps_thinking_effort_to_budget() -> None:
    params = ClaudeMessagesDriver()._params(
        _request(
            protocol=ModelProtocol.CLAUDE_API,
            base_url="https://api.anthropic.com/v1",
            model="claude-3-7-sonnet-latest",
            thinking_effort="high",
            max_output_tokens=10_000,
        ),
        stream=True,
    )

    assert params["thinking"] == {"type": "enabled", "budget_tokens": 8192}
    assert "temperature" not in params
    assert "top_p" not in params
