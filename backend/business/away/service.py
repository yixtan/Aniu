"""Away mode: what Aniu does on its own while you are not at the machine."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from backend.business.away.dto import AwayModeDTO, to_away_mode_dto
from backend.business.away.models import AwayMode, market_date
from backend.business.away.ports import AwayModeRepositoryPort, RunReportMailerPort
from backend.business.shared import CommitterPort

NowProvider = Callable[[], datetime]
logger = logging.getLogger(__name__)


class AwayModeService:
    """Reads and flips the switch, and acts on it when a run finishes."""

    def __init__(
        self,
        *,
        repo: AwayModeRepositoryPort,
        mailer: RunReportMailerPort,
        committer: CommitterPort | None = None,
        now_provider: NowProvider | None = None,
    ) -> None:
        self._repo = repo
        self._mailer = mailer
        self._committer = committer
        self._now = now_provider or (lambda: datetime.now(tz=UTC))

    async def get_state(self) -> AwayModeDTO:
        stored = await self._repo.get()
        return to_away_mode_dto(stored or AwayMode(), moment=self._now())

    async def set_enabled(self, enabled: bool) -> AwayModeDTO:
        """Switch away mode on for today, or off outright.

        Switching on stamps today's market date; it expires on its own when
        that date passes.
        """

        now = self._now()
        saved = await self._repo.save(
            AwayMode(active_date=market_date(now) if enabled else None)
        )
        await self._commit()
        logger.info(
            "away_mode_changed",
            extra={"enabled": enabled, "active_date": str(saved.active_date)},
        )
        return to_away_mode_dto(saved, moment=now)

    async def on_run_completed(self, run_id: int) -> None:
        """Mail the run's report when away mode is on for the current day.

        Never raises: the run has already succeeded by the time this is
        reached, and an email problem must not turn that into a failure.
        """

        try:
            stored = await self._repo.get()
            if stored is None or not stored.is_active(self._now()):
                return
            result = await self._mailer.send_run_report(run_id)
        except Exception:
            logger.warning(
                "away mode could not mail the run report",
                extra={"run_id": run_id},
                exc_info=True,
            )
            return
        if result.delivered:
            logger.info("away_mode_report_mailed", extra={"run_id": run_id})
        else:
            logger.warning(
                "away mode report was not delivered",
                extra={"run_id": run_id, "reason": result.message},
            )

    async def _commit(self) -> None:
        if self._committer is not None:
            await self._committer.commit()


__all__ = ["AwayModeService"]
