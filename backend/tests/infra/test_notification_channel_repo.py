"""Channel persistence, secret handling and fill watermarks."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.business.notifications import (
    NotificationChannelKind,
    TradeEventKind,
)
from backend.infra.db.models import NotificationChannelModel, SecretStoreModel
from backend.infra.repositories import (
    NotificationChannelRepository,
    NotificationFillWatermarkRepository,
)
from backend.infra.repositories.notification_channel_repo import SECRET_NAMESPACE


async def _create(repo: NotificationChannelRepository, **overrides: object):
    payload: dict[str, object] = {
        "name": "我的 webhook",
        "kind": NotificationChannelKind.WEBHOOK,
        "enabled": True,
        "subscribed_events": frozenset({TradeEventKind.ORDER_PLACED}),
        "body_template": None,
        "target_hint": "https://hook.test/…1234",
        "secret": "https://hook.test/abcd1234",
    }
    payload.update(overrides)
    return await repo.create(**payload)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_created_channel_round_trips_with_its_secret(session) -> None:
    repo = NotificationChannelRepository(session)

    channel = await _create(repo)

    assert channel.id > 0
    assert channel.subscribed_events == frozenset({TradeEventKind.ORDER_PLACED})
    assert await repo.get_secret(channel.id) == "https://hook.test/abcd1234"


@pytest.mark.asyncio
async def test_secret_is_stored_encrypted_outside_the_channel_row(session) -> None:
    repo = NotificationChannelRepository(session)
    channel = await _create(repo)

    model = await session.get(NotificationChannelModel, channel.id)
    stored = (
        await session.scalars(
            select(SecretStoreModel).where(
                SecretStoreModel.namespace == SECRET_NAMESPACE,
                SecretStoreModel.owner_id == str(channel.id),
            )
        )
    ).all()

    assert model is not None
    assert "abcd1234" not in str(model.__dict__)
    assert len(stored) == 1
    assert "abcd1234" not in stored[0].encrypted_value


@pytest.mark.asyncio
async def test_update_without_a_secret_keeps_the_stored_endpoint(session) -> None:
    repo = NotificationChannelRepository(session)
    channel = await _create(repo)

    updated = await repo.update(
        channel.id,
        name="改名了",
        enabled=False,
        subscribed_events=frozenset(TradeEventKind),
        body_template=None,
        target_hint=channel.target_hint,
        secret=None,
    )

    assert updated is not None
    assert updated.name == "改名了"
    assert updated.enabled is False
    assert await repo.get_secret(channel.id) == "https://hook.test/abcd1234"


@pytest.mark.asyncio
async def test_deleting_a_channel_removes_its_secret(session) -> None:
    repo = NotificationChannelRepository(session)
    channel = await _create(repo)

    assert await repo.delete(channel.id) is True
    assert await repo.get(channel.id) is None
    assert await repo.get_secret(channel.id) is None
    assert await repo.delete(channel.id) is False


@pytest.mark.asyncio
async def test_a_row_without_usable_events_falls_back_to_every_event(session) -> None:
    repo = NotificationChannelRepository(session)
    channel = await _create(repo)
    model = await session.get(NotificationChannelModel, channel.id)
    assert model is not None
    model.subscribed_events = ["not_an_event"]
    await session.flush()

    reloaded = await repo.get(channel.id)

    assert reloaded is not None
    assert reloaded.subscribed_events == frozenset(TradeEventKind)


@pytest.mark.asyncio
async def test_watermarks_persist_advance_and_prune(session) -> None:
    repo = NotificationFillWatermarkRepository(session)

    await repo.replace({"A1": 100, "B2": 200})
    assert await repo.load() == {"A1": 100, "B2": 200}

    await repo.replace({"A1": 300})
    assert await repo.load() == {"A1": 300}

    await repo.replace({})
    assert await repo.load() == {}
