"""What the run decided it may hold today, and what that decision cost.

A cap is a state, not an experience. Written into long-term memory it became
a fact about the world — six memories carried 「总敞口约20%封顶」 within six
days, each new rule inheriting it, and the one that finally documented it
derived the number from a tolerance nobody had chosen either. Memory holds how
to size given conditions; today's number belongs to today's run.

Declared rather than enforced. Nothing here refuses an order: the instrument
is that the number is stated before trading, recorded as a field rather than
as prose, and answerable to the review afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from backend.business.shared.trading.value_objects import utc_now

MAX_REASON_LENGTH = 2000


@dataclass(frozen=True, slots=True)
class ExposureCap:
    run_id: int
    cap_pct: float
    basis: str
    """What in today's market and the account's own record sets it here."""

    changed_from: str
    """What the last one was, and why this moved or did not.

    Required so that holding still is a stated decision. Six days of 「数字沿用
    未变」 passed without one run ever having to say why it was not moving.
    """

    forgone: str
    """What the cap stopped this run from doing today.

    The only evidence of what a cap costs. It was recorded once, on 9/16 —
    十余个更强信号全部只登记观察 — and never again, so there is nothing to
    weigh a cap against but the reasoning of whoever set it.
    """

    declared_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not 0 < self.cap_pct <= 100:
            raise ValueError("a cap is a percentage of the account")
        for name, value in (
            ("basis", self.basis),
            ("changed_from", self.changed_from),
            ("forgone", self.forgone),
        ):
            if not value.strip():
                raise ValueError(f"a declared cap must say its {name}")


__all__ = ["MAX_REASON_LENGTH", "ExposureCap"]
