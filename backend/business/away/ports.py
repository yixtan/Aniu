"""Ports for away mode."""

from __future__ import annotations

from typing import Protocol

from backend.business.away.models import AwayMode


class AwayModeRepositoryPort(Protocol):
    async def get(self) -> AwayMode | None: ...

    async def save(self, mode: AwayMode) -> AwayMode: ...


class RunReportMailOutcome(Protocol):
    """What a mail attempt reports back.

    Read-only so a frozen result object satisfies it.
    """

    @property
    def delivered(self) -> bool: ...

    @property
    def message(self) -> str: ...


class RunReportMailerPort(Protocol):
    """Mails one finished run's report.

    Declared here rather than imported from the reports feature so this module
    stays independent of it.
    """

    async def send_run_report(self, run_id: int) -> RunReportMailOutcome: ...


class RunCompletionHookPort(Protocol):
    """What the runs feature calls once a run has finished successfully.

    Implementations must never let their own failure surface into the caller:
    an unsent email may not fail a run that already succeeded.
    """

    async def on_run_completed(self, run_id: int) -> None: ...


__all__ = [
    "AwayModeRepositoryPort",
    "RunCompletionHookPort",
    "RunReportMailOutcome",
    "RunReportMailerPort",
]
