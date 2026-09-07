"""Notification channel API contract."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

BASE = "/api/aniu/notifications/channels"


async def _create(client: AsyncClient, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "我的 webhook",
        "kind": "webhook",
        "secret": "https://hook.test/abcd1234",
        "subscribed_events": ["order_placed", "order_filled"],
    }
    payload.update(overrides)
    response = await client.post(BASE, json=payload)
    assert response.status_code == 201, response.text
    created: dict[str, object] = response.json()
    return created


@pytest.mark.asyncio
async def test_channel_crud_never_returns_the_secret(api_client: AsyncClient) -> None:
    created = await _create(api_client)

    assert created["target_hint"] == "https://hook.test/…1234"
    assert "secret" not in created
    assert created["subscribed_events"] == ["order_placed", "order_filled"]

    listed = await api_client.get(BASE)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [created["id"]]
    assert all("secret" not in item for item in listed.json())

    updated = await api_client.put(
        f"{BASE}/{created['id']}",
        json={"name": "改名了", "enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "改名了"
    assert updated.json()["enabled"] is False
    # An omitted secret keeps the stored endpoint, so the hint is unchanged.
    assert updated.json()["target_hint"] == "https://hook.test/…1234"

    deleted = await api_client.delete(f"{BASE}/{created['id']}")
    assert deleted.status_code == 204
    assert await api_client.get(BASE) is not None
    assert (await api_client.get(BASE)).json() == []


@pytest.mark.asyncio
async def test_updating_a_missing_channel_returns_404(
    api_client: AsyncClient,
) -> None:
    response = await api_client.put(f"{BASE}/4321", json={"name": "x"})

    assert response.status_code == 404
    assert response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_test_send_delivers_to_the_configured_endpoint(
    api_client: AsyncClient,
    notification_endpoint,
) -> None:
    created = await _create(api_client)

    response = await api_client.post(f"{BASE}/{created['id']}/test")

    assert response.status_code == 200
    assert response.json()["delivered"] is True
    assert len(notification_endpoint.requests) == 1
    body = json.loads(notification_endpoint.requests[0].content)
    assert body["event"] == "order_placed"
    # The sample must look like a real push, direction included.
    assert body["direction"] == "buy"
    assert body["title"] == "Aniu 已下单 · 买入 600519"


@pytest.mark.asyncio
async def test_test_send_reports_an_unreachable_endpoint(
    api_client: AsyncClient,
    notification_endpoint,
) -> None:
    created = await _create(api_client)
    notification_endpoint.status_code = 500

    response = await api_client.post(f"{BASE}/{created['id']}/test")

    assert response.status_code == 200
    assert response.json()["delivered"] is False
    assert "HTTP 500" in response.json()["message"]


@pytest.mark.asyncio
async def test_vendor_channel_rejects_a_body_template(
    api_client: AsyncClient,
) -> None:
    response = await api_client.post(
        BASE,
        json={
            "name": "server酱",
            "kind": "serverchan",
            "secret": "SCTkey123",
            "body_template": '{"a": 1}',
        },
    )

    # Domain validation surfaces through the shared ValueError handler.
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ValidationError"
    assert "body_template" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_unknown_event_name_is_rejected(api_client: AsyncClient) -> None:
    response = await api_client.post(
        BASE,
        json={
            "name": "x",
            "kind": "webhook",
            "secret": "https://hook.test/x",
            "subscribed_events": ["order_exploded"],
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_channels_require_authentication(session_factory) -> None:
    from httpx import ASGITransport

    from backend.api.deps import get_session_factory
    from backend.main import app

    app.dependency_overrides[get_session_factory] = lambda: session_factory
    app.state.runtime.session_factory = session_factory
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(BASE)

    assert response.status_code == 401
    app.dependency_overrides.clear()
