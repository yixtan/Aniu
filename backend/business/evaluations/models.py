"""An independent review of one finished run.

An evaluation is not a stage of the run it reviews. It reaches no tool that
can trade, so it never holds the trading account, and it is not part of the
run FSM: a run is already terminal before one is ever requested. Keeping it
out of `strategy_runs` also keeps it off the timetable, which is a day's
schedule and has no row for something a person pressed a button for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from backend.business.shared.trading.value_objects import utc_now


class EvaluationStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


TERMINAL_STATUSES = frozenset({EvaluationStatus.COMPLETED, EvaluationStatus.FAILED})

MAX_CANDIDATES = 3
"""How many drafts one review offers.

Fewer than the five findings a run can carry, because these are suggestions
competing for a scarce slot, not a list to work through. Offering as many as
the cap allows invites filling it.
"""

MAX_CANDIDATE_LENGTH = 2000
"""What the raise endpoint accepts.

Enforced here too, so a drafted candidate is always something the person can
actually submit. A suggestion the form then refuses is worse than none.
"""


@dataclass(frozen=True, slots=True)
class FindingCandidate:
    """A finding the review drafted, which nobody has decided to raise.

    Drafting is not raising. The review writes the sentence and the test; what
    it cannot do is judge which objection is worth binding every future run to,
    and the one objection that has changed this account's strategy so far was
    not among the questions the reviewer asked — a person read the five and
    wrote a sixth.
    """

    finding: str
    resolution_test: str

    def __post_init__(self) -> None:
        if not self.finding.strip():
            raise ValueError("a candidate must say something")
        if not self.resolution_test.strip():
            # Same gate as OpenFinding: drafting one without a settling test
            # only moves the work of inventing one to the person.
            raise ValueError("a candidate must say what would settle it")


@dataclass(slots=True)
class Evaluation:
    run_id: int
    evaluation_id: int = 0
    status: EvaluationStatus = EvaluationStatus.PENDING
    questions: str | None = None
    answers: str | None = None
    candidates: tuple[FindingCandidate, ...] = ()
    total_tokens: int = 0
    cached_tokens: int = 0
    failure_reason: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def start(self) -> None:
        if self.status is not EvaluationStatus.PENDING:
            raise ValueError("only a pending evaluation can start")
        self.status = EvaluationStatus.RUNNING
        self.started_at = utc_now()

    def complete(
        self,
        *,
        questions: str,
        answers: str,
        candidates: tuple[FindingCandidate, ...] = (),
        total_tokens: int = 0,
        cached_tokens: int = 0,
    ) -> None:
        if self.status is not EvaluationStatus.RUNNING:
            raise ValueError("only a running evaluation can complete")
        self.status = EvaluationStatus.COMPLETED
        self.questions = questions.strip() or None
        self.answers = answers.strip() or None
        self.candidates = candidates
        self.total_tokens = max(0, total_tokens)
        # Never more than the total it sits inside, whatever the provider says.
        self.cached_tokens = min(max(0, cached_tokens), self.total_tokens)
        self.completed_at = utc_now()

    def fail(self, reason: str) -> None:
        if self.status is not EvaluationStatus.RUNNING:
            raise ValueError("only a running evaluation can fail")
        self.status = EvaluationStatus.FAILED
        self.failure_reason = reason.strip() or "evaluation failed"
        self.completed_at = utc_now()


__all__ = [
    "MAX_CANDIDATE_LENGTH",
    "TERMINAL_STATUSES",
    "Evaluation",
    "EvaluationStatus",
    "FindingCandidate",
]
