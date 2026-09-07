"""Application service for push notification channels and delivery."""

from __future__ import annotations

import logging

from backend.business.notifications.commands import (
    CreateChannelCommand,
    UpdateChannelCommand,
)
from backend.business.notifications.dto import (
    NotificationChannelDTO,
    NotificationTestResultDTO,
    mask_secret,
    to_notification_channel_dto,
)
from backend.business.notifications.models import (
    NotificationChannel,
    TradeDirection,
    TradeEventKind,
    TradeNotificationEvent,
    normalize_body_template,
    normalize_channel_name,
    normalize_subscribed_events,
)
from backend.business.notifications.ports import (
    NotificationChannelRepositoryPort,
    NotificationSenderPort,
)
from backend.business.shared import CommitterPort, NotificationChannelNotFoundError

logger = logging.getLogger(__name__)

# Shaped exactly like a real push so the test message is representative.
_TEST_EVENT = TradeNotificationEvent(
    kind=TradeEventKind.ORDER_PLACED,
    instruction="买入 600519 1700 100",
    direction=TradeDirection.BUY,
    stock_code="600519",
    stock_name="贵州茅台",
    price=1700.0,
    quantity=100,
    order_id="TEST-0000000000",
)


def _normalized_secret(value: object) -> str:
    secret = "" if value is None else str(value).strip()
    if not secret:
        raise ValueError("channel secret must not be empty")
    return secret


class NotificationService:
    """Manage push channels and fan one trade event out to each subscriber."""

    def __init__(
        self,
        *,
        channel_repo: NotificationChannelRepositoryPort,
        sender: NotificationSenderPort,
        committer: CommitterPort | None = None,
    ) -> None:
        self._channel_repo = channel_repo
        self._sender = sender
        self._committer = committer

    async def list_channels(self) -> list[NotificationChannelDTO]:
        channels = await self._channel_repo.list_channels()
        return [to_notification_channel_dto(channel) for channel in channels]

    async def create_channel(
        self, command: CreateChannelCommand
    ) -> NotificationChannelDTO:
        secret = _normalized_secret(command.secret)
        body_template = normalize_body_template(command.body_template)
        channel = await self._channel_repo.create(
            name=normalize_channel_name(command.name),
            kind=command.kind,
            enabled=bool(command.enabled),
            subscribed_events=normalize_subscribed_events(command.subscribed_events),
            body_template=body_template,
            target_hint=mask_secret(secret),
            secret=secret,
        )
        await self._commit()
        return to_notification_channel_dto(channel)

    async def update_channel(
        self, command: UpdateChannelCommand
    ) -> NotificationChannelDTO:
        current = await self._require_channel(command.channel_id)
        secret: str | None = None
        if command.provided("secret"):
            raw = command.secret
            candidate = "" if raw is None else str(raw).strip()
            # A blank secret means "keep the stored one", so the client never has
            # to round-trip a credential it was not shown.
            secret = candidate or None
        updated = await self._channel_repo.update(
            command.channel_id,
            name=(
                normalize_channel_name(str(command.name))
                if command.provided("name")
                else current.name
            ),
            enabled=(
                bool(command.enabled)
                if command.provided("enabled")
                else current.enabled
            ),
            subscribed_events=(
                normalize_subscribed_events(command.subscribed_events)
                if command.provided("subscribed_events")
                else current.subscribed_events
            ),
            body_template=(
                normalize_body_template(
                    None
                    if command.body_template is None
                    else str(command.body_template)
                )
                if command.provided("body_template")
                else current.body_template
            ),
            target_hint=(
                mask_secret(secret) if secret is not None else current.target_hint
            ),
            secret=secret,
        )
        if updated is None:
            raise NotificationChannelNotFoundError(command.channel_id)
        await self._commit()
        return to_notification_channel_dto(updated)

    async def delete_channel(self, channel_id: int) -> None:
        if not await self._channel_repo.delete(channel_id):
            raise NotificationChannelNotFoundError(channel_id)
        await self._commit()

    async def send_test(self, channel_id: int) -> NotificationTestResultDTO:
        channel = await self._require_channel(channel_id)
        secret = await self._channel_repo.get_secret(channel_id)
        if not secret:
            return NotificationTestResultDTO(
                channel_id=channel_id,
                delivered=False,
                message="通道缺少地址或密钥，请重新填写后保存。",
            )
        try:
            await self._sender.send(channel=channel, secret=secret, event=_TEST_EVENT)
        except Exception as exc:
            logger.warning(
                "notification test delivery failed",
                extra={"channel_id": channel_id, "channel_kind": channel.kind.value},
            )
            return NotificationTestResultDTO(
                channel_id=channel_id,
                delivered=False,
                message=f"发送失败：{exc}",
            )
        return NotificationTestResultDTO(
            channel_id=channel_id,
            delivered=True,
            message="测试消息已发送，请在对应客户端确认。",
        )

    async def publish(self, event: TradeNotificationEvent) -> int:
        """Fan one event out to every subscribed channel.

        Delivery problems are logged and swallowed: a push must never fail the
        run or account refresh that produced the event.
        """

        try:
            channels = await self._channel_repo.list_channels()
        except Exception:
            logger.exception("failed to load notification channels")
            return 0
        delivered = 0
        for channel in channels:
            if not channel.wants(event.kind):
                continue
            try:
                secret = await self._channel_repo.get_secret(channel.id)
                if not secret:
                    logger.warning(
                        "skipping notification channel without a stored endpoint",
                        extra={"channel_id": channel.id},
                    )
                    continue
                await self._sender.send(channel=channel, secret=secret, event=event)
            except Exception:
                logger.warning(
                    "notification delivery failed",
                    extra={
                        "channel_id": channel.id,
                        "channel_kind": channel.kind.value,
                        "event": event.kind.value,
                    },
                    exc_info=True,
                )
                continue
            delivered += 1
        return delivered

    async def _require_channel(self, channel_id: int) -> NotificationChannel:
        channel = await self._channel_repo.get(channel_id)
        if channel is None:
            raise NotificationChannelNotFoundError(channel_id)
        return channel

    async def _commit(self) -> None:
        if self._committer is not None:
            await self._committer.commit()


__all__ = ["NotificationService"]
