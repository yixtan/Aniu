"""Adapter exposing one run's presentable report to the reports feature."""

from __future__ import annotations

from dataclasses import dataclass

from backend.infra.repositories.run_repo import RunRepository


@dataclass(slots=True)
class RunReportQuery:
    repository: RunRepository

    async def get_report(self, run_id: int) -> tuple[str, str] | None:
        run = await self.repository.get_by_id(run_id)
        if run is None or run.summary is None:
            return None
        return run.summary, run.summary_render_mode


__all__ = ["RunReportQuery"]
