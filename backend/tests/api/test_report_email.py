"""Run report email settings and send endpoints."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from backend.infra.repositories import RunRepository

BASE = "/api/aniu/report-email"


async def _configure(client: AsyncClient, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "sender": "aniu@example.com",
        "recipient": "me@example.com",
        "api_key": "re_secret_ABCD",
    }
    payload.update(overrides)
    response = await client.put(BASE, json=payload)
    assert response.status_code == 200, response.text
    saved: dict[str, object] = response.json()
    return saved


@pytest.mark.asyncio
async def test_settings_start_unconfigured(api_client: AsyncClient) -> None:
    response = await api_client.get(BASE)

    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["api_key_configured"] is False


@pytest.mark.asyncio
async def test_saving_never_returns_the_key(api_client: AsyncClient) -> None:
    saved = await _configure(api_client)

    assert saved["configured"] is True
    assert saved["api_key_configured"] is True
    assert saved["api_key_last_four"] == "ABCD"
    assert "api_key" not in saved
    assert "re_secret_ABCD" not in json.dumps(saved)


@pytest.mark.asyncio
async def test_a_malformed_address_is_rejected(api_client: AsyncClient) -> None:
    response = await api_client.put(
        BASE,
        json={"sender": "not-an-email", "recipient": "me@example.com", "api_key": "k"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ValidationError"


@pytest.mark.asyncio
async def test_sending_before_configuration_returns_503(
    api_client: AsyncClient,
) -> None:
    response = await api_client.post(f"{BASE}/runs/1")

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_sending_a_missing_run_returns_404(api_client: AsyncClient) -> None:
    await _configure(api_client)

    response = await api_client.post(f"{BASE}/runs/99999")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_stored_report_is_mailed(
    api_client: AsyncClient,
    session_factory,
    notification_endpoint,
) -> None:
    await _configure(api_client)

    # The endpoint reads the run through its repository, so seed one directly.
    from backend.business.runs import StrategyRun, StrategySnapshot
    from backend.business.shared.enums import TriggerSource

    async with session_factory() as session:
        repo = RunRepository(session)
        seeded = StrategyRun(
            run_id=20260907900,
            trigger_source=TriggerSource.MANUAL,
            schedule_id=None,
            snapshot=StrategySnapshot(
                prompt_version="v1", risk_rules_version="risk-v1"
            ),
        )
        seeded.set_summary("<section><h2>执行总结</h2></section>", render_mode="html")
        await repo.save(seeded)
        await session.commit()

    response = await api_client.post(f"{BASE}/runs/20260907900")

    assert response.status_code == 200, response.text
    assert response.json()["delivered"] is True
    assert len(notification_endpoint.requests) == 1
    body = json.loads(notification_endpoint.requests[0].content)
    assert body["to"] == ["me@example.com"]
    assert "执行总结" in body["html"]


@pytest.mark.asyncio
async def test_endpoints_require_authentication(session_factory) -> None:
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
