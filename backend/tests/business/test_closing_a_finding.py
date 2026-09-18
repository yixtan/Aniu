"""What settles an objection, and what closing one leaves behind.

Finding 1 is the case these are drawn from. It was disposed ADJUSTED three
times on the strength of the run's own plan, and only the fourth pointed at
something outside it — three shallow orders filled, three deep ones did not.
Any rule that closed on repeated agreement would have closed it an hour
before the evidence existed.
"""

from __future__ import annotations

import pytest

from backend.business.open_findings import FindingStatus, OpenFinding, Verdict

FINDING = "五笔买入限价单全属 AI 硬件链，成交条件与风险条件相同。"
TEST = "说明在什么行情下它们会分批而非同时成交，或把敞口拆到不相关的主线上。"


def _one() -> OpenFinding:
    return OpenFinding(finding=FINDING, resolution_test=TEST, finding_id=1)


def test_closing_must_say_why() -> None:
    """Without it, settled-by-evidence and abandoned-as-wrong are one row."""

    with pytest.raises(ValueError, match="must say why"):
        _one().close("   ")


def test_a_closed_finding_keeps_its_reason() -> None:
    item = _one()
    item.close("浅档三笔分批成交、深档三笔未触发，了结条件由行情满足。")

    assert item.status is FindingStatus.CLOSED
    assert item.closing_note.startswith("浅档三笔")
    assert item.closed_at is not None


def test_a_row_closed_before_this_existed_still_loads() -> None:
    """Validated on close, not on load — refusing these would lose the
    finding itself, which is worse than the missing sentence."""

    item = OpenFinding(
        finding=FINDING,
        resolution_test=TEST,
        status=FindingStatus.CLOSED,
        closing_note="",
    )

    assert item.status is FindingStatus.CLOSED


def test_proposing_settlement_does_not_close_anything() -> None:
    """The run says the test is met; the operator decides whether it is."""

    item = _one()
    item.dispose(run_id=20260918105, verdict=Verdict.SETTLED, note="三笔分批成交。")

    assert item.status is FindingStatus.OPEN
    assert item.settlement_proposed is True


def test_settling_is_not_being_talked_past() -> None:
    """The amber count exists to catch a run answering without acting, and
    naming the test met is the opposite of that."""

    item = _one()
    item.dispose(run_id=1, verdict=Verdict.SETTLED, note="三笔分批成交。")

    assert item.times_disputed == 0


def test_only_the_newest_disposition_asks_for_a_decision() -> None:
    """One called settled on Tuesday and adjusted again on Wednesday is not
    waiting on anybody; a badge left standing would read as a backlog."""

    item = _one()
    item.dispose(run_id=1, verdict=Verdict.SETTLED, note="看起来达成了。")
    item.dispose(run_id=2, verdict=Verdict.ADJUSTED, note="又改了结构。")

    assert item.settlement_proposed is False


def test_a_closed_finding_is_never_waiting_on_anyone() -> None:
    item = _one()
    item.dispose(run_id=1, verdict=Verdict.SETTLED, note="三笔分批成交。")
    item.close("确认了结。")

    assert item.settlement_proposed is False
