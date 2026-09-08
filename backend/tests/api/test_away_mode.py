"""Away mode switch endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from backend.business.away import market_date

BASE = "/api/aniu/away-mode"


@pytest.mark.asyncio
async def test_away_mode_starts_off(api_client: AsyncClient) -> None:
    response = await api_client.get(BASE)

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["active_date"] is None


@pytest.mark.asyncio
async def test_switching_on_reports_todays_market_day(api_client: AsyncClient) -> None:
    response = await api_client.put(BASE, json={"enabled": True})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is True
    assert body["active_date"] == market_date(datetime.now(tz=UTC)).isoformat()


@pytest.mark.asyncio
async def test_the_switch_survives_a_reread(api_client: AsyncClient) -> None:
    await api_client.put(BASE, json={"enabled": True})

    body = (await api_client.get(BASE)).json()

    assert body["enabled"] is True


@pytest.mark.asyncio
async def test_switching_off_clears_the_day(api_client: AsyncClient) -> None:
    await api_client.put(BASE, json={"enabled": True})

    body = (await api_client.put(BASE, json={"enabled": False})).json()

    assert body["enabled"] is False
    assert body["active_date"] is None


@pytest.mark.asyncio
async def test_an_unknown_field_is_rejected(api_client: AsyncClient) -> None:
    response = await api_client.put(BASE, json={"enabled": True, "until": "forever"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_the_switch_needs_authentication(session_factory) -> None:
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
