"""The tool that puts a run's verdict on the finding, not only in prose."""

from __future__ import annotations

import pytest

from backend.business.open_findings import (
    FindingStatus,
    OpenFinding,
    OpenFindingService,
    Verdict,
)
from backend.infra.integrations.open_finding_agent_tools import DisposeOpenFindingTool
from backend.infra.repositories.open_finding_repo import OpenFindingRepository

RUN_ID = 20260917112


async def _raise_one(session_factory) -> int:
    async with session_factory() as session:
        stored = await OpenFindingRepository(session).add(
            OpenFinding(
                finding="五笔买入限价单全属 AI 硬件链",
                resolution_test="说明在什么行情下它们会分批而非同时成交",
            )
        )
        await session.commit()
    return stored.finding_id


@pytest.mark.asyncio
async def test_a_verdict_lands_on_the_finding(session_factory) -> None:
    """Stated only in the report, a verdict cannot be counted.

    On 2026-09-17 a run answered an open finding and changed its strategy
    because of it — and the finding still read "已被 0 次运行处置", because
    nothing wrote the answer back.
    """

    finding_id = await _raise_one(session_factory)
    tool = DisposeOpenFindingTool(session_factory)

    result = await tool.run_for_call(
        run_id=RUN_ID,
        tool_call_id="call-1",
        finding_id=finding_id,
        verdict="ADJUSTED",
        note="单链笔数上限收紧为 2 笔，敞口拆到 CRO 与重卡",
    )

    assert result == {
        "status": "ok",
        "finding_id": finding_id,
        "verdict": "ADJUSTED",
        "times_disputed": 0,
        "settlement_proposed": False,
    }
    async with session_factory() as session:
        stored = await OpenFindingRepository(session).get_by_id(finding_id)
    assert stored is not None
    assert [item.verdict for item in stored.dispositions] == [Verdict.ADJUSTED]
    assert stored.dispositions[0].run_id == RUN_ID
    # Acting on it is not disputing it.
    assert stored.times_disputed == 0


@pytest.mark.asyncio
async def test_answering_without_acting_is_counted(session_factory) -> None:
    """The count is the mechanism: talked past has to be as visible as done."""

    finding_id = await _raise_one(session_factory)
    tool = DisposeOpenFindingTool(session_factory)

    await tool.run_for_call(
        run_id=RUN_ID,
        tool_call_id="c1",
        finding_id=finding_id,
        verdict="DISAGREED",
        note="经复核，该顾虑不成立",
    )
    result = await tool.run_for_call(
        run_id=RUN_ID + 1,
        tool_call_id="c2",
        finding_id=finding_id,
        verdict="UNDECIDED",
        note="需要各链的历史回踩同步率，我手上没有",
    )

    assert result == {
        "status": "ok",
        "finding_id": finding_id,
        "verdict": "UNDECIDED",
        "times_disputed": 2,
        "settlement_proposed": False,
    }


@pytest.mark.asyncio
async def test_a_verdict_with_no_reasoning_is_refused(session_factory) -> None:
    """A verdict nobody can check against the finding's own test is noise."""

    finding_id = await _raise_one(session_factory)
    tool = DisposeOpenFindingTool(session_factory)

    with pytest.raises(ValueError, match="requires a note"):
        await tool.run_for_call(
            run_id=RUN_ID,
            tool_call_id="c1",
            finding_id=finding_id,
            verdict="ADJUSTED",
            note="   ",
        )


@pytest.mark.asyncio
async def test_the_tool_cannot_close_a_finding(session_factory) -> None:
    """Closing is the operator's. Given it, a run would write 「经复核，该顾虑
    不成立」 on the third pass and move on."""

    finding_id = await _raise_one(session_factory)
    tool = DisposeOpenFindingTool(session_factory)
    definition = tool.to_tool_definition()

    assert "CLOSED" not in str(definition["parameters"])
    await tool.run_for_call(
        run_id=RUN_ID,
        tool_call_id="c1",
        finding_id=finding_id,
        verdict="ADJUSTED",
        note="已调整",
    )
    async with session_factory() as session:
        stored = await OpenFindingRepository(session).get_by_id(finding_id)
    assert stored is not None
    assert stored.status is FindingStatus.OPEN


@pytest.mark.asyncio
async def test_a_verdict_on_something_that_is_not_there_says_so(
    session_factory,
) -> None:
    tool = DisposeOpenFindingTool(session_factory)

    with pytest.raises(ValueError, match="no such open finding"):
        await tool.run_for_call(
            run_id=RUN_ID,
            tool_call_id="c1",
            finding_id=999,
            verdict="ADJUSTED",
            note="已调整",
        )


@pytest.mark.asyncio
async def test_a_closed_finding_takes_no_further_verdict(session_factory) -> None:
    finding_id = await _raise_one(session_factory)
    async with session_factory() as session:
        await OpenFindingService(OpenFindingRepository(session)).close(
            finding_id, note="按证据了结。"
        )
        await session.commit()

    with pytest.raises(ValueError, match="closed finding"):
        await DisposeOpenFindingTool(session_factory).run_for_call(
            run_id=RUN_ID,
            tool_call_id="c1",
            finding_id=finding_id,
            verdict="ADJUSTED",
            note="已调整",
        )


@pytest.mark.asyncio
async def test_a_run_may_ask_for_closure_but_not_perform_it(
    session_factory,
) -> None:
    """SETTLED is a request, not a close. The run points at evidence outside
    its own plan; whether that evidence settles the matter stays with the
    operator, because a run that could close would write 「经复核，该顾虑不成立」
    and move on."""

    finding_id = await _raise_one(session_factory)
    tool = DisposeOpenFindingTool(session_factory)

    result = await tool.run_for_call(
        run_id=20260918105,
        tool_call_id="call-1",
        finding_id=finding_id,
        verdict="SETTLED",
        note="浅档三笔成交、深档三笔未触发，分批而非同日齐发已由行情证实。",
    )

    assert result == {
        "status": "ok",
        "finding_id": finding_id,
        "verdict": "SETTLED",
        "times_disputed": 0,
        "settlement_proposed": True,
    }
    async with session_factory() as session:
        stored = await OpenFindingRepository(session).get_by_id(finding_id)
    assert stored is not None
    assert stored.status is FindingStatus.OPEN


@pytest.mark.asyncio
async def test_the_tool_offers_settled_without_offering_close(
    session_factory,
) -> None:
    definition = DisposeOpenFindingTool(session_factory).to_tool_definition()
    verdicts = definition["parameters"]["properties"]["verdict"]["enum"]

    assert verdicts == ["ADJUSTED", "SETTLED", "DISAGREED", "UNDECIDED"]
    assert "CLOSED" not in verdicts
