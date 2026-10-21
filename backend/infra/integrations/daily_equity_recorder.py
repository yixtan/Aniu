"""Isolate history writes and make their failures visible without failing refresh."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.performance import DailyEquity
from backend.business.performance.models import EquityRecordingStatus
from backend.infra.repositories.daily_equity_repo import DailyEquityRepository
from backend.infra.repositories.equity_recording_status_repo import (
    EquityRecordingStatusRepository,
)

logger = logging.getLogger(__name__)


def _failure_message(exc: Exception) -> str:
    # Driver errors may contain SQL, account amounts or credentials. Publish
    # only known categories; the original exception stays in the redacted log.
    message = str(exc).lower()
    if "disk is full" in message or "no space left" in message:
        return "存储空间不足，请清理磁盘后刷新账户。"
    if "locked" in message or "busy" in message:
        return "数据库正忙，请稍后刷新账户。"
    if "readonly" in message or "read-only" in message:
        return "数据库不可写，请检查数据目录的写入权限。"
    return "净值写入失败，请查看后端日志；排除问题后刷新账户。"


class DailyEquityRecorder:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._latest: EquityRecordingStatus | None = None

    def _remember(self, status: EquityRecordingStatus) -> None:
        if self._latest is None or status.attempted_at >= self._latest.attempted_at:
            self._latest = status

    async def recording_status(self) -> EquityRecordingStatus | None:
        async with self._session_factory() as session:
            persisted = await EquityRecordingStatusRepository(
                session
            ).recording_status()
        if persisted is not None:
            self._remember(persisted)
        return self._latest

    async def record_day(self, equity: DailyEquity) -> None:
        attempted_at = datetime.now(tz=UTC)
        previous = self._latest
        try:
            async with self._session_factory() as session:
                status_repo = EquityRecordingStatusRepository(session)
                persisted = await status_repo.recording_status()
                if persisted is not None:
                    previous = persisted
                await DailyEquityRepository(session).record_day(equity)
                success = EquityRecordingStatus(
                    trade_date=equity.trade_date,
                    attempted_at=attempted_at,
                    last_success_at=attempted_at,
                    error_message=None,
                )
                await status_repo.save(success)
                await session.commit()
            self._remember(success)
        except Exception as exc:
            failure = EquityRecordingStatus(
                trade_date=equity.trade_date,
                attempted_at=attempted_at,
                last_success_at=previous.last_success_at if previous else None,
                error_message=_failure_message(exc),
            )
            self._remember(failure)
            # The failed transaction has been rolled back by the session exit.
            # If even this write fails, keep a process-local warning for GETs.
            try:
                async with self._session_factory() as session:
                    await EquityRecordingStatusRepository(session).save(failure)
                    await session.commit()
            except Exception:
                logger.warning(
                    "equity recording status was not persisted", exc_info=True
                )
            raise
