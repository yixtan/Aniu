"""Declaring the cap: recorded, refused when unreasoned, never enforcing."""

from __future__ import annotations

import pytest

from backend.business.exposure import ExposureCapService
from backend.infra.db.models import StrategyRunModel
from backend.infra.integrations.exposure_cap_agent_tools import DeclareExposureCapTool
from backend.infra.repositories.exposure_cap_repo import ExposureCapRepository

RUN_ID = 20260921101

REASONS = {
    "basis": "指数站上 20 日线，四条不相关主线。",
    "changed_from": "上次 20%，今天放宽，因为相关性下降。",
    "forgone": "未触及上限。",
}


async def _finished_run(session_factory) -> None:
    async with session_factory() as session:
        session.add(
            StrategyRunModel(
                id=RUN_ID,
                trigger_source="SCHEDULED",
                status="COMPLETED",
                current_state="Completed",
                snapshot_json={},
                trace_json={},
                summary_render_mode="html",
                started_at="2026-09-21T01:30:00+00:00",
                completed_at="2026-09-21T01:40:00+00:00",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_a_declaration_lands_on_the_record(session_factory) -> None:
    await _finished_run(session_factory)
    tool = DeclareExposureCapTool(session_factory)

    result = await tool.run_for_call(
        run_id=RUN_ID, tool_call_id="call-1", cap_pct=26.5, **REASONS
    )

    assert result == {"status": "ok", "cap_pct": 26.5, "run_id": RUN_ID}
    async with session_factory() as session:
        stored = await ExposureCapService(ExposureCapRepository(session)).for_run(
            RUN_ID
        )
    assert stored is not None
    assert stored.cap_pct == 26.5


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["basis", "changed_from", "forgone"])
async def test_a_number_without_its_reasons_is_refused(
    session_factory, missing: str
) -> None:
    """Otherwise this is the memory again, with a different filing cabinet."""

    await _finished_run(session_factory)
    tool = DeclareExposureCapTool(session_factory)
    reasons = {**REASONS, missing: "  "}

    with pytest.raises(ValueError, match=missing):
        await tool.run_for_call(
            run_id=RUN_ID, tool_call_id="call-1", cap_pct=20.0, **reasons
        )


@pytest.mark.asyncio
async def test_the_tool_says_the_number_is_the_runs_to_choose(
    session_factory,
) -> None:
    """It was a constant the agent had written for itself and then inherited
    into every new rule; the description has to say it is not one."""

    definition = DeclareExposureCapTool(session_factory).to_tool_definition()

    assert "不是别处规定的常量" in definition["description"]
    assert "不要写进长期记忆" in definition["description"]
    properties = definition["parameters"]["properties"]
    assert set(properties) == {"cap_pct", "basis", "changed_from", "forgone"}
    assert definition["parameters"]["required"] == [
        "cap_pct",
        "basis",
        "changed_from",
        "forgone",
    ]
    # 「沿用未变」 is what six days of not deciding looked like.
    assert "不是理由" in properties["changed_from"]["description"]


@pytest.mark.asyncio
async def test_the_tool_only_runs_in_the_analysis_stage(session_factory) -> None:
    assert DeclareExposureCapTool(session_factory).enabled_stages == ("Run",)
