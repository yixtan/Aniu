"""Drafting the findings a review suggests, without letting it cost the review.

The drafts are a shortcut past typing up an objection. The questions and
answers underneath are what was actually asked for, so nothing here may take
them down with it.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.agent.kernel.runtime_config import LlmRuntimeConfig
from backend.business.evaluations import MAX_CANDIDATE_LENGTH, MAX_CANDIDATES
from backend.business.fill_record import FillRecord
from backend.business.settings import AppSettings
from backend.infra.integrations import evaluation_agent as module
from backend.infra.integrations.evaluation_agent import (
    EvaluationAgent,
    parse_candidates,
)
from backend.llm import ModelProtocol

RUN_ID = 20260917112


def test_a_fenced_array_still_parses() -> None:
    """Models wrap JSON in a code fence unprompted; that is not a malformed
    answer, it is the same answer."""

    parsed = parse_candidates(
        '好的。\n```json\n[{"finding": "五笔全在一条链上。", '
        '"resolution_test": "把敞口拆到不相关的主线上。"}]\n```'
    )

    assert len(parsed) == 1
    assert parsed[0].finding == "五笔全在一条链上。"


def test_one_bad_entry_costs_only_that_entry() -> None:
    parsed = parse_candidates(
        '[{"finding": "甲", "resolution_test": "一"}, '
        '"不是对象", '
        '{"finding": "乙", "resolution_test": "二"}]'
    )

    assert [item.finding for item in parsed] == ["甲", "乙"]


def test_a_draft_without_a_settling_test_is_dropped() -> None:
    """The same gate a raised finding passes. A draft missing it only moves
    the work of inventing one back to the person."""

    parsed = parse_candidates(
        '[{"finding": "甲", "resolution_test": "  "}, '
        '{"finding": "乙", "resolution_test": "二"}]'
    )

    assert [item.finding for item in parsed] == ["乙"]


def test_prose_instead_of_json_drafts_nothing() -> None:
    assert parse_candidates("我觉得这次运行挺好的，没什么可提的。") == ()


def test_more_drafts_than_offered_are_cut() -> None:
    payload = ", ".join(
        f'{{"finding": "第{index}条", "resolution_test": "了结{index}"}}'
        for index in range(MAX_CANDIDATES + 3)
    )
    parsed = parse_candidates(f"[{payload}]")

    assert len(parsed) == MAX_CANDIDATES


def test_the_same_draft_twice_is_offered_once() -> None:
    parsed = parse_candidates(
        '[{"finding": "甲", "resolution_test": "一"}, '
        '{"finding": "甲", "resolution_test": "另一种说法"}]'
    )

    assert len(parsed) == 1


def test_a_draft_is_always_short_enough_to_submit() -> None:
    """Trimmed to what the raise endpoint accepts, so every draft on the page
    is one the person can raise without editing it first."""

    long_text = "很" * (MAX_CANDIDATE_LENGTH + 500)
    parsed = parse_candidates(
        f'[{{"finding": "{long_text}", "resolution_test": "{long_text}"}}]'
    )

    assert len(parsed[0].finding) == MAX_CANDIDATE_LENGTH
    assert len(parsed[0].resolution_test) == MAX_CANDIDATE_LENGTH


class _Usage:
    def __init__(self, total: int, cached: int) -> None:
        self.total_tokens = total
        self.cache_read = cached


class _Reply:
    def __init__(self, content: str, total: int = 0, cached: int = 0) -> None:
        self.content = content
        self.usage = _Usage(total, cached)


class _StubHarness:
    """Stands in for AgentHarness, one scripted reply per label."""

    replies: dict[str, Any] = {}
    labels: list[str] = []

    def __init__(self, **kwargs: Any) -> None:
        self._label = str(kwargs["label"])

    async def prompt(self, _message: str) -> Any:
        _StubHarness.labels.append(self._label)
        reply = _StubHarness.replies[self._label]
        if isinstance(reply, Exception):
            raise reply
        return reply


class _StubRuntimeFactory:
    async def build_stage_runtime(self, _settings: object) -> LlmRuntimeConfig:
        return LlmRuntimeConfig(
            protocol=ModelProtocol.OPENAI_CHAT_COMPLETIONS,
            base_url="https://example.invalid",
            api_key="test",
            model="test-model",
        )


class _StubSettingsRepo:
    async def get(self) -> AppSettings:
        return AppSettings()


class _StubContextReader:
    async def read(self, run_id: int) -> tuple[None, FillRecord]:
        return None, FillRecord(run_id=run_id, own_orders=(), days=(), unattributed=0)


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> EvaluationAgent:
    _StubHarness.labels = []
    monkeypatch.setattr(module, "AgentHarness", _StubHarness)
    return EvaluationAgent(
        llm_client=object(),  # type: ignore[arg-type]
        runtime_factory=_StubRuntimeFactory(),  # type: ignore[arg-type]
        settings_repo=_StubSettingsRepo(),  # type: ignore[arg-type]
        context_reader=_StubContextReader(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_a_review_drafts_candidates_after_reading_the_answers(
    agent: EvaluationAgent,
) -> None:
    """Drafted from the answers, not the questions alone: what the run conceded
    under questioning is the part nobody knew before asking."""

    _StubHarness.replies = {
        "Evaluate": _Reply("一、证伪条件是什么？", total=100, cached=10),
        "Answer": _Reply("这个数我手上没有。", total=200, cached=20),
        "Draft": _Reply(
            '[{"finding": "五笔全在一条链上，会同一天一起成交。", '
            '"resolution_test": "把敞口拆到不相关的主线上。"}]',
            total=50,
            cached=5,
        ),
    }

    result = await agent.evaluate(RUN_ID)

    assert _StubHarness.labels == ["Evaluate", "Answer", "Draft"]
    assert len(result.candidates) == 1
    assert result.candidates[0].finding.startswith("五笔全在一条链上")
    # Billed with the review it rides on, or the page under-reports the cost.
    assert result.total_tokens == 350
    assert result.cached_tokens == 35


@pytest.mark.asyncio
async def test_a_failed_draft_leaves_the_review_standing(
    agent: EvaluationAgent,
) -> None:
    """Two calls have already been paid for and the review is already complete;
    a provider error on the convenience must not throw that away."""

    _StubHarness.replies = {
        "Evaluate": _Reply("一、证伪条件是什么？", total=100, cached=10),
        "Answer": _Reply("这个数我手上没有。", total=200, cached=20),
        "Draft": RuntimeError("provider refused the request"),
    }

    result = await agent.evaluate(RUN_ID)

    assert result.questions == "一、证伪条件是什么？"
    assert result.answers == "这个数我手上没有。"
    assert result.candidates == ()
    assert result.total_tokens == 300


@pytest.mark.asyncio
async def test_an_empty_review_is_not_sent_to_be_drafted(
    agent: EvaluationAgent,
) -> None:
    """Drafts are made of the questions and answers; with neither there is
    nothing to make them from, and a third call would only bill for asking."""

    _StubHarness.replies = {
        "Evaluate": _Reply("", total=100, cached=10),
        "Answer": _Reply("", total=200, cached=20),
    }

    result = await agent.evaluate(RUN_ID)

    assert _StubHarness.labels == ["Evaluate", "Answer"]
    assert result.candidates == ()
