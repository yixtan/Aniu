"""Tests for metrics derived from persisted two-stage traces."""

from backend.business.runs import (
    metrics_from_trace_payload,
    run_stage_status_from_trace_payload,
)


def test_metrics_use_only_completed_run_stage_trade_count() -> None:
    trace = {
        "stages": [
            {
                "key": "run",
                "status": "completed",
                "steps": [{"type": "result", "data": {"trade_count": 2}}],
            },
            {
                "key": "summary",
                "status": "completed",
                "steps": [{"type": "result", "data": {"trade_count": 8}}],
            },
        ]
    }

    assert metrics_from_trace_payload(trace) == (0, 0, 0, 2)
    assert run_stage_status_from_trace_payload(trace) == "completed"


def test_tool_metrics_use_model_content_characters_after_redaction() -> None:
    trace = {
        "stages": [
            {
                "key": "run",
                "status": "completed",
                "steps": [
                    {
                        "type": "tool",
                        "data": {
                            "arguments": {"x": "a"},
                            "model_content_characters": 42,
                            "result": "this legacy payload must not be counted",
                        },
                    }
                ],
            }
        ]
    }

    assert metrics_from_trace_payload(trace) == (1, 0, 13, 0)


def test_non_completed_run_does_not_report_trade_count() -> None:
    trace = {
        "stages": [
            {
                "key": "run",
                "status": "failed",
                "steps": [{"type": "result", "data": {"trade_count": 3}}],
            }
        ]
    }

    assert metrics_from_trace_payload(trace)[3] == 0
    assert run_stage_status_from_trace_payload(trace) == "failed"


def test_a_reported_token_count_beats_the_character_estimate() -> None:
    """The estimate reads each piece of text once; a tool loop re-sends the
    whole conversation every turn and is billed for it again, so it runs two
    to three times low. Whenever the provider says, the provider wins."""

    trace = {
        "stages": [
            {
                "key": "run",
                "status": "completed",
                "steps": [
                    {"type": "thinking", "content": "推理" * 500},
                    {
                        "type": "result",
                        "data": {"total_tokens": 185_984, "trade_count": 1},
                    },
                ],
            }
        ]
    }

    _, _, tokens, trades = metrics_from_trace_payload(trace)

    assert tokens == 185_984
    assert trades == 1


def test_reported_counts_are_summed_across_stages() -> None:
    trace = {
        "stages": [
            {
                "key": "run",
                "status": "completed",
                "steps": [{"type": "result", "data": {"total_tokens": 100}}],
            },
            {
                "key": "summary",
                "status": "completed",
                "steps": [{"type": "result", "data": {"total_tokens": 40}}],
            },
        ]
    }

    assert metrics_from_trace_payload(trace)[2] == 140


def test_the_estimate_still_applies_where_no_provider_reported_usage() -> None:
    """An OpenAI-compatible endpoint only reports usage when asked, so a
    channel without `stream_options` records zero — which must read as
    "unknown", not as "this run was free"."""

    trace = {
        "stages": [
            {
                "key": "run",
                "status": "completed",
                "steps": [
                    {"type": "thinking", "content": "x" * 400},
                    {"type": "result", "data": {"total_tokens": 0}},
                ],
            }
        ]
    }

    assert metrics_from_trace_payload(trace)[2] == 100
