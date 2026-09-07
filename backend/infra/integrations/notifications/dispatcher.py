"""Background dispatch of trade notifications.

Delivery runs off the caller's task so a slow or unreachable receiver can never
add latency to a strategy run or an account refresh, and never fails one.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine, Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.business.notifications import (
    NotificationEvent,
    NotificationSenderPort,
    NotificationService,
    OrderFillObservation,
    detect_fill_events,
)
from backend.infra.repositories.notification_channel_repo import (
    NotificationChannelRepository,
    NotificationDeliveryRepository,
    NotificationFillWatermarkRepository,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_PENDING = 32


class TradeNotificationDispatcher:
    """Fan trade events out to configured channels, off the caller's task."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        sender: NotificationSenderPort,
        max_pending: int = DEFAULT_MAX_PENDING,
    ) -> None:
        self._session_factory = session_factory
        self._sender = sender
        self._max_pending = max_pending
        self._pending: set[asyncio.Task[None]] = set()

    async def publish(self, event: NotificationEvent) -> None:
        self._spawn(self._deliver(event), label=event.kind.value)

    async def announce_fills(
        self, observations: Sequence[OrderFillObservation]
    ) -> None:
        if not observations:
            return
        self._spawn(self._deliver_fills(tuple(observations)), label="order_filled")

    async def aclose(self) -> None:
        """Wait for in-flight deliveries so shutdown does not drop a push."""

        pending = tuple(self._pending)
        if not pending:
            return
        done, timed_out = await asyncio.wait(pending, timeout=15.0)
        del done
        for task in timed_out:
            task.cancel()

    def _spawn(
        self,
        coro: Coroutine[object, object, None],
        *,
        label: str,
    ) -> None:
        if len(self._pending) >= self._max_pending:
            coro.close()
            logger.warning(
                "dropping trade notification: dispatch queue is full",
                extra={"event": label, "pending": len(self._pending)},
            )
            return
        task: asyncio.Task[None] = asyncio.create_task(
            coro, name=f"aniu-notify-{label}"
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _deliver(self, event: NotificationEvent) -> None:
        try:
            async with self._session_factory() as session:
                await self._service(session).publish(event)
        except Exception:
            logger.warning(
                "trade notification dispatch failed",
                extra={"event": event.kind.value},
                exc_info=True,
            )

    async def _deliver_fills(
        self, observations: tuple[OrderFillObservation, ...]
    ) -> None:
        try:
            async with self._session_factory() as session:
                watermarks = NotificationFillWatermarkRepository(session)
                announced = await watermarks.load()
                # No watermarks at all means this install has never observed an
                # order list. Treat it as a baseline instead of announcing every
                # historical fill still inside the upstream window.
                result = detect_fill_events(
                    observations, announced, cold_start=not announced
                )
                service = self._service(session)
                for event in result.events:
                    await service.publish(event)
                # The watermark advances even when every channel failed, so one
                # broken receiver cannot turn every refresh into a resend storm.
                await watermarks.replace(result.watermarks)
                await session.commit()
        except Exception:
            logger.warning(
                "order fill notification dispatch failed",
                extra={"observations": len(observations)},
                exc_info=True,
            )

    def _service(self, session: AsyncSession) -> NotificationService:
        return NotificationService(
            channel_repo=NotificationChannelRepository(session),
            sender=self._sender,
            delivery_repo=NotificationDeliveryRepository(session),
            committer=session,
        )


__all__ = ["DEFAULT_MAX_PENDING", "TradeNotificationDispatcher"]
