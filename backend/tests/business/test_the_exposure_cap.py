"""The cap a run sets for itself, and why it is a record rather than a rule.

It used to be a fact in long-term memory. Six memories carried 「总敞口约20%
封顶」 within six days, each new rule inheriting it, and when a finding asked
where the number came from the run wrote a derivation — 容忍回撤 1% ÷ 单腿极端
亏损 5% — whose only market-facing trigger fed terms with no market state in
them. It recomputed to 20% by construction, and said so: 数字沿用未变.
"""

from __future__ import annotations

import pytest

from backend.business.exposure import ExposureCap, ExposureCapService
from backend.infra.db.models import StrategyRunModel
from backend.infra.repositories.exposure_cap_repo import ExposureCapRepository

RUN_ID = 20260921101


def _run(session, run_id: int = RUN_ID) -> None:
    session.add(
        StrategyRunModel(
            id=run_id,
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


def _cap(run_id: int = RUN_ID, pct: float = 20.0) -> ExposureCap:
    return ExposureCap(
        run_id=run_id,
        cap_pct=pct,
        basis="指数站上 20 日线，持仓分属四条不相关主线。",
        changed_from="上次 20%，今天不动，因为相关性没有进一步下降。",
        forgone="兆易创新 +15.4 亿净流入，符合买入标准，因额度未买。",
    )


@pytest.mark.parametrize("missing", ["basis", "changed_from", "forgone"])
def test_a_cap_without_its_reasons_is_just_the_number_again(missing: str) -> None:
    """The reasons are the instrument. Without them this is 129 with a new
    home: a number that arrives already decided."""

    fields = {
        "basis": "指数站上 20 日线。",
        "changed_from": "上次 20%。",
        "forgone": "未触及上限。",
    }
    fields[missing] = "   "

    with pytest.raises(ValueError, match=missing):
        ExposureCap(run_id=RUN_ID, cap_pct=20.0, **fields)


def test_holding_still_is_a_decision_that_has_to_be_stated() -> None:
    """Six days passed at 20% without one run ever having to say why it was
    not moving, because nothing ever asked."""

    cap = _cap()

    assert "今天不动" in cap.changed_from


@pytest.mark.parametrize("pct", [0, -5, 101])
def test_a_cap_is_a_share_of_the_account(pct: float) -> None:
    with pytest.raises(ValueError, match="percentage"):
        ExposureCap(
            run_id=RUN_ID,
            cap_pct=pct,
            basis="x",
            changed_from="x",
            forgone="x",
        )


@pytest.mark.asyncio
async def test_a_declaration_survives_a_round_trip(session) -> None:
    _run(session)
    await session.commit()

    service = ExposureCapService(ExposureCapRepository(session))
    await service.declare(_cap(pct=25.0))
    await session.commit()

    stored = await ExposureCapRepository(session).for_run(RUN_ID)

    assert stored is not None
    assert stored.cap_pct == 25.0
    assert stored.forgone.startswith("兆易创新")


@pytest.mark.asyncio
async def test_declaring_twice_is_one_decision_not_two(session) -> None:
    """A run that declares again changed its mind; two rows would read as two
    runs, and the series is the whole point of keeping them."""

    _run(session)
    await session.commit()

    service = ExposureCapService(ExposureCapRepository(session))
    await service.declare(_cap(pct=20.0))
    await service.declare(_cap(pct=30.0))
    await session.commit()

    series = await ExposureCapRepository(session).recent(limit=10)

    assert len(series) == 1
    assert series[0].cap_pct == 30.0


@pytest.mark.asyncio
async def test_the_series_reads_newest_first(session) -> None:
    """One number says what the cap is. Only the series says whether it moves."""

    for offset in range(3):
        _run(session, RUN_ID + offset)
    await session.commit()

    service = ExposureCapService(ExposureCapRepository(session))
    for offset, pct in enumerate((20.0, 24.0, 18.0)):
        await service.declare(_cap(RUN_ID + offset, pct))
    await session.commit()

    series = await ExposureCapRepository(session).recent(limit=10)

    assert [cap.cap_pct for cap in series] == [18.0, 24.0, 20.0]
    latest = await ExposureCapRepository(session).latest()
    assert latest is not None
    assert latest.cap_pct == 18.0
