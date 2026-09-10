"""Application service for push notification channels and delivery."""

from __future__ import annotations

import logging
from dataclasses import replace

from backend.business.notifications.commands import (
    CreateChannelCommand,
    UpdateChannelCommand,
)
from backend.business.notifications.dto import (
    NotificationChannelDTO,
    NotificationDeliveryPageDTO,
    NotificationTestResultDTO,
    mask_secret,
    to_notification_channel_dto,
    to_notification_delivery_dto,
)
from backend.business.notifications.models import (
    DeliveryStatus,
    NotificationChannel,
    NotificationDelivery,
    NotificationEvent,
    NotificationEventKind,
    TradeDirection,
    normalize_body_template,
    normalize_channel_name,
    normalize_subscribed_events,
)
from backend.business.notifications.ports import (
    NotificationChannelRepositoryPort,
    NotificationDeliveryRepositoryPort,
    NotificationSenderPort,
    StockNameLookupPort,
)
from backend.business.shared import CommitterPort, NotificationChannelNotFoundError

logger = logging.getLogger(__name__)

# Shaped exactly like a real push so the test message is representative.
_TEST_EVENT = NotificationEvent(
    kind=NotificationEventKind.ORDER_PLACED,
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
        delivery_repo: NotificationDeliveryRepositoryPort | None = None,
        committer: CommitterPort | None = None,
        names: StockNameLookupPort | None = None,
    ) -> None:
        self._channel_repo = channel_repo
        self._sender = sender
        self._delivery_repo = delivery_repo
        self._committer = committer
        self._names = names

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
            await self._record(
                channel,
                _TEST_EVENT,
                error="通道缺少地址或密钥，未发送。",
                is_test=True,
            )
            await self._commit()
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
            await self._record(channel, _TEST_EVENT, error=str(exc), is_test=True)
            await self._commit()
            return NotificationTestResultDTO(
                channel_id=channel_id,
                delivered=False,
                message=f"发送失败：{exc}",
            )
        await self._record(channel, _TEST_EVENT, is_test=True)
        await self._commit()
        return NotificationTestResultDTO(
            channel_id=channel_id,
            delivered=True,
            message="测试消息已发送，请在对应客户端确认。",
        )

    async def _named(self, event: NotificationEvent) -> NotificationEvent:
        """Fill in the company name when the event only carries a code.

        A fill is observed on the order list, which names the company; an
        order and a cancel are read back from the instruction the agent typed,
        which is only ever "买入 300394 268 200". Resolving it here rather than
        where the event is built keeps the lookup off the run's task — this
        runs on the dispatcher's, where a slow provider costs nothing.

        Best effort throughout: a push that names only the code still tells the
        operator what happened.
        """

        if self._names is None or not event.stock_code or event.stock_name:
            return event
        try:
            name = await self._names.name_for(event.stock_code)
        except Exception:
            logger.warning(
                "stock name lookup failed for a notification",
                extra={"stock_code": event.stock_code},
                exc_info=True,
            )
            return event
        return event if not name else replace(event, stock_name=name)

    async def publish(self, event: NotificationEvent) -> int:
        """Fan one event out to every subscribed channel.

        Delivery problems are logged and swallowed: a push must never fail the
        run or account refresh that produced the event.
        """

        try:
            channels = await self._channel_repo.list_channels()
        except Exception:
            logger.exception("failed to load notification channels")
            return 0
        event = await self._named(event)
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
                    await self._record(
                        channel, event, error="通道缺少地址或密钥，未发送。"
                    )
                    continue
                await self._sender.send(channel=channel, secret=secret, event=event)
            except Exception as exc:
                logger.warning(
                    "notification delivery failed",
                    extra={
                        "channel_id": channel.id,
                        "channel_kind": channel.kind.value,
                        "event": event.kind.value,
                    },
                    exc_info=True,
                )
                await self._record(channel, event, error=str(exc))
                continue
            await self._record(channel, event)
            delivered += 1
        # History rows are written by this method, so the unit of work has to be
        # closed here; the background dispatcher does not commit on its own.
        await self._commit()
        return delivered

    async def list_deliveries(
        self, *, limit: int, offset: int
    ) -> NotificationDeliveryPageDTO:
        if self._delivery_repo is None:
            return NotificationDeliveryPageDTO(items=(), total=0)
        rows = await self._delivery_repo.list_page(limit=limit, offset=offset)
        return NotificationDeliveryPageDTO(
            items=tuple(to_notification_delivery_dto(row) for row in rows),
            total=await self._delivery_repo.count(),
        )

    async def _record(
        self,
        channel: NotificationChannel,
        event: NotificationEvent,
        *,
        error: str | None = None,
        is_test: bool = False,
    ) -> None:
        """Append one history row. Never raises: history must not break a push."""

        if self._delivery_repo is None:
            return
        try:
            await self._delivery_repo.append(
                NotificationDelivery(
                    id=0,
                    channel_id=channel.id,
                    channel_name=channel.name,
                    channel_kind=channel.kind,
                    event_kind=event.kind,
                    title=event.title,
                    status=(
                        DeliveryStatus.FAILED if error else DeliveryStatus.DELIVERED
                    ),
                    error_message=error,
                    is_test=is_test,
                )
            )
        except Exception:
            logger.warning(
                "failed to record notification delivery",
                extra={"channel_id": channel.id, "event": event.kind.value},
                exc_info=True,
            )

    async def _require_channel(self, channel_id: int) -> NotificationChannel:
        channel = await self._channel_repo.get(channel_id)
        if channel is None:
            raise NotificationChannelNotFoundError(channel_id)
        return channel

    async def _commit(self) -> None:
        if self._committer is not None:
            await self._committer.commit()


__all__ = ["NotificationService"]
