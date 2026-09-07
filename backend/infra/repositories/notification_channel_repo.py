"""Persistence for push channels and announced-fill watermarks."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.business.notifications import (
    NotificationChannel,
    NotificationChannelKind,
    TradeEventKind,
)
from backend.infra.db.models import (
    NotificationChannelModel,
    NotificationFillWatermarkModel,
)
from backend.infra.repositories.secret_store_repo import SecretStoreRepository
from backend.infra.security import SecretCodec

SECRET_NAMESPACE = "notification_channel"
SECRET_NAME = "endpoint"


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(tz=UTC)


class NotificationChannelRepository:
    """Channel rows plus their encrypted endpoint in the shared secret store."""

    def __init__(
        self,
        session: AsyncSession,
        secret_codec: SecretCodec | None = None,
    ) -> None:
        self._session = session
        self._secrets = SecretStoreRepository(session, secret_codec=secret_codec)

    async def list_channels(self) -> list[NotificationChannel]:
        statement = select(NotificationChannelModel).order_by(
            NotificationChannelModel.id
        )
        rows = (await self._session.scalars(statement)).all()
        return [self._to_entity(row) for row in rows]

    async def get(self, channel_id: int) -> NotificationChannel | None:
        model = await self._session.get(NotificationChannelModel, channel_id)
        return None if model is None else self._to_entity(model)

    async def create(
        self,
        *,
        name: str,
        kind: NotificationChannelKind,
        enabled: bool,
        subscribed_events: frozenset[TradeEventKind],
        body_template: str | None,
        target_hint: str,
        secret: str,
    ) -> NotificationChannel:
        model = NotificationChannelModel(
            name=name,
            kind=kind.value,
            enabled=enabled,
            subscribed_events=sorted(event.value for event in subscribed_events),
            body_template=body_template,
            target_hint=target_hint,
        )
        self._session.add(model)
        # The secret is addressed by channel id, so the row must exist first.
        await self._session.flush()
        await self._secrets.set_secret(
            SECRET_NAMESPACE, str(model.id), SECRET_NAME, secret
        )
        return self._to_entity(model)

    async def update(
        self,
        channel_id: int,
        *,
        name: str,
        enabled: bool,
        subscribed_events: frozenset[TradeEventKind],
        body_template: str | None,
        target_hint: str,
        secret: str | None,
    ) -> NotificationChannel | None:
        model = await self._session.get(NotificationChannelModel, channel_id)
        if model is None:
            return None
        model.name = name
        model.enabled = enabled
        model.subscribed_events = sorted(event.value for event in subscribed_events)
        model.body_template = body_template
        model.target_hint = target_hint
        if secret is not None:
            await self._secrets.set_secret(
                SECRET_NAMESPACE, str(channel_id), SECRET_NAME, secret
            )
        await self._session.flush()
        return self._to_entity(model)

    async def delete(self, channel_id: int) -> bool:
        model = await self._session.get(NotificationChannelModel, channel_id)
        if model is None:
            return False
        await self._secrets.delete_owner(SECRET_NAMESPACE, str(channel_id))
        await self._session.delete(model)
        await self._session.flush()
        return True

    async def get_secret(self, channel_id: int) -> str | None:
        try:
            return await self._secrets.get_secret(
                SECRET_NAMESPACE, str(channel_id), SECRET_NAME
            )
        except ValueError:
            # A secret encrypted with a key that is no longer available must not
            # take the whole dispatch down; the channel is reported as unset.
            return None

    def _to_entity(self, model: NotificationChannelModel) -> NotificationChannel:
        return NotificationChannel(
            id=model.id,
            name=model.name,
            kind=NotificationChannelKind(model.kind),
            enabled=bool(model.enabled),
            subscribed_events=frozenset(
                TradeEventKind(value)
                for value in (model.subscribed_events or [])
                if value in frozenset(item.value for item in TradeEventKind)
            )
            or frozenset(TradeEventKind),
            body_template=model.body_template,
            target_hint=model.target_hint or "",
            created_at=_parse_datetime(model.created_at),
            updated_at=_parse_datetime(model.updated_at),
        )


class NotificationFillWatermarkRepository:
    """Announced fill quantity per upstream order id."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load(self) -> dict[str, int]:
        rows = (
            await self._session.scalars(select(NotificationFillWatermarkModel))
        ).all()
        return {row.order_id: int(row.announced_quantity) for row in rows}

    async def replace(self, watermarks: Mapping[str, int]) -> None:
        """Persist the new watermark set, dropping orders that aged out.

        The upstream order list only covers the current window, so entries that
        disappear from it are pruned instead of growing without bound.
        """

        if not watermarks:
            await self._session.execute(delete(NotificationFillWatermarkModel))
            await self._session.flush()
            return
        existing = {
            row.order_id: row
            for row in (
                await self._session.scalars(select(NotificationFillWatermarkModel))
            ).all()
        }
        now = datetime.now(tz=UTC).isoformat()
        for order_id, quantity in watermarks.items():
            model = existing.pop(order_id, None)
            if model is None:
                self._session.add(
                    NotificationFillWatermarkModel(
                        order_id=order_id,
                        announced_quantity=int(quantity),
                        updated_at=now,
                    )
                )
            elif model.announced_quantity != int(quantity):
                model.announced_quantity = int(quantity)
                model.updated_at = now
        for stale in existing.values():
            await self._session.delete(stale)
        await self._session.flush()


__all__ = [
    "SECRET_NAME",
    "SECRET_NAMESPACE",
    "NotificationChannelRepository",
    "NotificationFillWatermarkRepository",
]
