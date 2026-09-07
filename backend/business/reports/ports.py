"""Ports for run report email delivery."""

from __future__ import annotations

from typing import Protocol

from backend.business.reports.models import EmailDeliverySettings, RunReportMail


class EmailSettingsRepositoryPort(Protocol):
    async def get(self) -> EmailDeliverySettings | None: ...

    async def save(
        self, settings: EmailDeliverySettings, *, api_key: str | None
    ) -> EmailDeliverySettings: ...

    async def get_api_key(self) -> str | None: ...


class RunReportQueryPort(Protocol):
    """Reads one run's presentable report.

    Declared here rather than imported from the runs feature so this module
    stays independent of it.
    """

    async def get_report(self, run_id: int) -> tuple[str, str] | None:
        """Return ``(summary, render_mode)`` or ``None`` when the run has none."""
        ...


class ReportMailerPort(Protocol):
    """Transport for one report email. Raises on delivery failure."""

    async def send(
        self,
        *,
        api_key: str,
        sender: str,
        recipient: str,
        mail: RunReportMail,
    ) -> None: ...


__all__ = [
    "EmailSettingsRepositoryPort",
    "ReportMailerPort",
    "RunReportQueryPort",
]
