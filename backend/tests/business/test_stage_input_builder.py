"""Tests for the Summary input: the report alone, within every budget."""

from __future__ import annotations

import pytest

from backend.business.runs.execution import RunReport
from backend.business.runs.stage_input import (
    SummaryInputTooLargeError,
    build_summary_stage_payload,
)
from backend.business.shared.serialization import serialize_context
from backend.llm import estimate_tokens


def _report() -> RunReport:
    return RunReport(
        content="# Run report\n\nActual conclusion.",
        transcript=(
            {"role": "assistant", "reasoning": "old reasoning " * 100},
            {"role": "tool", "content": "ignored"},
            {"role": "assistant", "reasoning": "new reasoning " * 100},
        ),
        tool_activity=(
            {
                "tool_call_id": "query-1",
                "tool_name": "query_quote",
                "status": "ok",
                "content": {"rows": ["market data " * 500]},
            },
            {
                "tool_call_id": "trade-1",
                "tool_name": "trade",
                "status": "ok",
                "content": {"success": True, "data": {"orderId": "123"}},
            },
        ),
    )


def test_the_summary_is_sent_the_report_and_nothing_else() -> None:
    """Its prompt only lays the report out, adding and dropping nothing.

    On 2026-09-29 the reasoning sent beside the report was 66,000 to 125,000
    characters, against reports of 2,500 to 3,400.
    """

    payload = build_summary_stage_payload(_report(), max_characters=1_000_000)

    assert payload == {"run_report_markdown": "# Run report\n\nActual conclusion."}


def test_a_report_over_the_budget_is_refused_not_clipped() -> None:
    with pytest.raises(SummaryInputTooLargeError, match="exceeds summary budget"):
        build_summary_stage_payload(RunReport(content="r" * 1_000), max_characters=500)


def test_empty_budget_is_rejected() -> None:
    """The message CLAUDE.md tells you to look for when summaries fall back
    to Markdown because the output cap equals the context window."""

    with pytest.raises(SummaryInputTooLargeError, match="budget is empty"):
        build_summary_stage_payload(RunReport(content="report"), max_characters=0)


def test_token_budget_bounds_a_cjk_report_by_estimated_tokens() -> None:
    report = RunReport(content="结论" * 800)

    payload = build_summary_stage_payload(report, max_tokens=1_800)

    assert estimate_tokens(serialize_context(payload)) <= 1_800
    with pytest.raises(SummaryInputTooLargeError):
        build_summary_stage_payload(report, max_tokens=1_000)


def test_a_byte_budget_binds_where_a_token_budget_would_not() -> None:
    """Chinese runs about three bytes a character, so a report can sit inside
    a token budget and still be too big for a gateway that counts bytes —
    which is exactly how the v2ex relay once refused a Summary call."""

    report = RunReport(content="# 运行报告\n\n" + "本次运行的结论与依据。" * 200)
    serialized = serialize_context(
        build_summary_stage_payload(report, max_tokens=100_000)
    )
    byte_size = len(serialized.encode("utf-8"))

    with pytest.raises(SummaryInputTooLargeError):
        build_summary_stage_payload(
            report, max_tokens=100_000, max_bytes=byte_size // 2
        )


def test_an_empty_budget_on_any_axis_leaves_nothing_to_send() -> None:
    with pytest.raises(SummaryInputTooLargeError):
        build_summary_stage_payload(_report(), max_tokens=100_000, max_bytes=0)


def test_no_budget_at_all_is_still_refused() -> None:
    with pytest.raises(SummaryInputTooLargeError):
        build_summary_stage_payload(_report())
