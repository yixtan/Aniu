"""An objection that has not been answered yet.

Three things already have a home here: what is always true goes in the prompt,
what to do in a given situation goes in memory, and what to do with a resting
order goes in the order plan. An unanswered objection is none of those. It is
not a rule, because it may turn out to be wrong; it is not an experience,
because nothing has been learned yet. Filed as memory it sits beside the rules
the agent wrote for itself and is pruned by a nightly pass that cannot tell
them apart — which is how a rule defining "no fill" as compliance survived
three days of zero fills while the objection to it would not have.

So it stays a question, in front of the run, until a person closes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from backend.business.shared.trading.value_objects import utc_now

MAX_OPEN_FINDINGS = 5
"""How many reach a run at once.

A cap, not a preference. Every open finding is answered in every run, and the
Run stage already went from 101 to 345 seconds in three days; an unbounded
list of objections would be paid for in lost order-watch slots.
"""


class FindingStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class Verdict(StrEnum):
    """What a run said about a finding. Silence is not one of them."""

    ADJUSTED = "ADJUSTED"
    SETTLED = "SETTLED"
    """The run believes the resolution test is now met. It still cannot close.

    Separate from ADJUSTED because "I changed what you asked about" and "the
    thing you said would settle this has happened" are answered by different
    evidence. The first is the run's own plan; the second has to point at
    something outside it. Finding 1 was disposed ADJUSTED three times on its
    own account before a fill demonstrated it, and a rule that closed on
    repeated agreement would have closed it an hour before the evidence.
    """

    DISAGREED = "DISAGREED"
    UNDECIDED = "UNDECIDED"


ACTED_ON = frozenset({Verdict.ADJUSTED, Verdict.SETTLED})
"""Verdicts that are not a run talking past the objection."""


@dataclass(frozen=True, slots=True)
class Disposition:
    run_id: int
    verdict: Verdict
    note: str
    at: datetime


@dataclass(slots=True)
class OpenFinding:
    finding: str
    resolution_test: str
    finding_id: int = 0
    evaluation_id: int | None = None
    status: FindingStatus = FindingStatus.OPEN
    dispositions: tuple[Disposition, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    closed_at: datetime | None = None
    closing_note: str = ""
    """Why it closed. Not validated on load: rows closed before this existed
    have none, and refusing to read them would lose the finding itself."""

    def __post_init__(self) -> None:
        if not self.finding.strip():
            raise ValueError("a finding must say something")
        if not self.resolution_test.strip():
            # The gate against "是否考虑了流动性风险". An objection nobody can
            # settle is paid for in every future run and never leaves the list.
            raise ValueError("a finding must say what would settle it")

    @property
    def times_disputed(self) -> int:
        """How often a run answered and did not act.

        Visible so that an objection being talked past is as legible as one
        being addressed. A run may state a disposition; only a person closes.
        """

        return sum(item.verdict not in ACTED_ON for item in self.dispositions)

    @property
    def settlement_proposed(self) -> bool:
        """Whether the run is waiting on a person, right now.

        The latest disposition only. An open finding that was called settled
        on Tuesday and adjusted again on Wednesday is not waiting on anybody;
        it moved on, and a badge left standing would be read as a backlog.
        """

        if self.status is not FindingStatus.OPEN or not self.dispositions:
            return False
        return self.dispositions[-1].verdict is Verdict.SETTLED

    def dispose(self, *, run_id: int, verdict: Verdict, note: str) -> None:
        if self.status is not FindingStatus.OPEN:
            raise ValueError("a closed finding takes no further disposition")
        self.dispositions = (
            *self.dispositions,
            Disposition(
                run_id=run_id,
                verdict=verdict,
                note=note.strip(),
                at=utc_now(),
            ),
        )

    def close(self, note: str) -> None:
        """Say why, in one line.

        The same gate as the resolution test, at the other end: "settled, the
        fills came in three batches" and "dropped, it was the wrong question"
        are the same row without it, and a month later nobody can tell which
        of them a closed finding was.
        """

        if not note.strip():
            raise ValueError("closing a finding must say why")
        if self.status is FindingStatus.CLOSED:
            return
        self.status = FindingStatus.CLOSED
        self.closing_note = note.strip()
        self.closed_at = utc_now()


__all__ = [
    "ACTED_ON",
    "MAX_OPEN_FINDINGS",
    "Disposition",
    "FindingStatus",
    "OpenFinding",
    "Verdict",
]
