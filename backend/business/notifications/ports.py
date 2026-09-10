"""Ports for the notifications feature."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from backend.business.notifications.fill_tracker import OrderFillObservation
from backend.business.notifications.models import (
    NotificationChannel,
    NotificationChannelKind,
    NotificationDelivery,
    NotificationEvent,
    NotificationEventKind,
)


class NotificationChannelRepositoryPort(Protocol):
    async def list_channels(self) -> list[NotificationChannel]: ...

    async def get(self, channel_id: int) -> NotificationChannel | None: ...

    async def create(
        self,
        *,
        name: str,
        kind: NotificationChannelKind,
        enabled: bool,
        subscribed_events: frozenset[NotificationEventKind],
        body_template: str | None,
        target_hint: str,
        secret: str,
    ) -> NotificationChannel: ...

    async def update(
        self,
        channel_id: int,
        *,
        name: str,
        enabled: bool,
        subscribed_events: frozenset[NotificationEventKind],
        body_template: str | None,
        target_hint: str,
        secret: str | None,
    ) -> NotificationChannel | None: ...

    async def delete(self, channel_id: int) -> bool: ...

    async def get_secret(self, channel_id: int) -> str | None: ...


class NotificationDeliveryRepositoryPort(Protocol):
    async def append(self, delivery: NotificationDelivery) -> NotificationDelivery: ...

    async def list_page(
        self, *, limit: int, offset: int
    ) -> list[NotificationDelivery]: ...

    async def count(self) -> int: ...


class FillWatermarkRepositoryPort(Protocol):
    """Filled quantity already announced, keyed by upstream order id."""

    async def load(self) -> dict[str, int]: ...

    async def replace(self, watermarks: Mapping[str, int]) -> None: ...


class StockNameLookupPort(Protocol):
    """Names a company from its code.

    Declared here rather than reached for across features: `business` may not
    import `stock_api`, and a port belongs to the feature that consumes it.
    Returning ``None`` means the name could not be established, which is never
    a reason to hold back the push.
    """

    async def name_for(self, symbol: str) -> str | None: ...


class NotificationSenderPort(Protocol):
    """Transport for one push. Raises on delivery failure."""

    async def send(
        self,
        *,
        channel: NotificationChannel,
        secret: str,
        event: NotificationEvent,
    ) -> None: ...


class OrderFillNotifierPort(Protocol):
    """What the account feature calls after fetching a fresh order list.

    The implementation owns the announced-quantity watermark, so callers hand
    over the raw observations and never track what was already sent.
    """

    async def announce_fills(
        self, observations: Sequence[OrderFillObservation]
    ) -> None: ...


class NotificationPublisherPort(Protocol):
    """What the run and account features call to announce a trade moment.

    Implementations must never let a delivery problem surface into the caller:
    a failed push may not fail a run or an account refresh.
    """

    async def publish(self, event: NotificationEvent) -> None: ...


__all__ = [
    "FillWatermarkRepositoryPort",
    "NotificationChannelRepositoryPort",
    "NotificationDeliveryRepositoryPort",
    "OrderFillNotifierPort",
    "NotificationSenderPort",
    "NotificationPublisherPort",
    "StockNameLookupPort",
]
