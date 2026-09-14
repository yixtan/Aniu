"""Reading the provider a run used out of its frozen snapshot."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.infra.db.models import ModelProfileModel, StrategyRunModel
from backend.infra.repositories.system_status_repo import SystemStatusRepository

MOMENT = datetime(2026, 9, 14, 1, 30, tzinfo=UTC)


async def _add_run(session, run_id: int, stage_models: dict[str, object]) -> None:
    session.add(
        StrategyRunModel(
            id=run_id,
            trigger_source="scheduled",
            status="COMPLETED",
            current_state="Summary",
            snapshot_json={"stage_models": stage_models},
            trace_json={},
            started_at=MOMENT.isoformat(),
            completed_at=MOMENT.isoformat(),
            total_tokens=100,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_an_analysis_is_attributed_to_the_channel_its_run_stage_froze(
    session,
) -> None:
    """Read in SQL, so a wrong path fails silently as "unknown" rather than
    raising — which is why it is pinned here rather than left to review."""

    await _add_run(
        session,
        20260914101,
        {
            "Run": {"channel_profile_id": 2, "model_name": "coder-ds4"},
            # Summary is deliberately not consulted: it renders, it does not
            # think, and the per-stage token split needed to bill it apart is
            # absent from most stored traces.
            "Summary": {"channel_profile_id": 3, "model_name": "gpt-4.1-mini"},
        },
    )

    facts = await SystemStatusRepository(session).runs_since(
        datetime(2026, 9, 1, tzinfo=UTC)
    )

    assert [fact.channel_id for fact in facts] == [2]


@pytest.mark.asyncio
async def test_a_watch_is_attributed_to_its_own_stage(session) -> None:
    """A watch freezes only Watch, so the Run path is absent and must fall
    through rather than leaving the run unattributed."""

    await _add_run(
        session,
        20260914301,
        {"Watch": {"channel_profile_id": 5, "model_name": "deepseek-flash"}},
    )

    facts = await SystemStatusRepository(session).runs_since(
        datetime(2026, 9, 1, tzinfo=UTC)
    )

    assert [fact.channel_id for fact in facts] == [5]


@pytest.mark.asyncio
async def test_a_run_that_froze_nothing_reports_no_channel(session) -> None:
    await _add_run(session, 20260914102, {})

    facts = await SystemStatusRepository(session).runs_since(
        datetime(2026, 9, 1, tzinfo=UTC)
    )

    assert [fact.channel_id for fact in facts] == [None]


@pytest.mark.asyncio
async def test_channel_names_come_back_by_id(session) -> None:
    session.add(
        ModelProfileModel(
            id=2,
            name="v2ex",
            model_name="coder-ds4",
            base_url="https://example.test",
        )
    )
    await session.flush()

    assert await SystemStatusRepository(session).channel_names() == {2: "v2ex"}
