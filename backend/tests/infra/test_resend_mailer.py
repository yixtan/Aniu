"""Resend transport behaviour for run report emails."""

from __future__ import annotations

import json

import httpx
import pytest

from backend.business.reports import RunReportMail
from backend.business.shared import ServiceIntegrationError
from backend.infra.integrations.email import ResendReportMailer
from backend.infra.integrations.email.resend import MAX_BODY_BYTES

MAIL = RunReportMail(
    run_id=20260907116,
    subject="Aniu 运行报告 · #20260907116",
    html="<section><h2>执行总结</h2></section>",
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _send(handler, mail: RunReportMail = MAIL) -> None:
    async with _client(handler) as client:
        await ResendReportMailer(client).send(
            api_key="re_test_key",
            sender="aniu@example.com",
            recipient="me@example.com",
            mail=mail,
        )


@pytest.mark.asyncio
async def test_the_report_html_is_posted_as_the_message_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "abc-123"})

    await _send(handler)

    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["auth"] == "Bearer re_test_key"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["from"] == "aniu@example.com"
    assert body["to"] == ["me@example.com"]
    assert body["subject"] == MAIL.subject
    assert body["html"] == MAIL.html


@pytest.mark.asyncio
async def test_a_rejected_request_surfaces_the_provider_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "You can only send testing emails to your own address"},
        )

    with pytest.raises(ServiceIntegrationError, match="your own address"):
        await _send(handler)


@pytest.mark.asyncio
async def test_a_rejection_without_a_body_still_reports_the_status() -> None:
    with pytest.raises(ServiceIntegrationError, match="HTTP 500"):
        await _send(lambda request: httpx.Response(500, text="oops"))


@pytest.mark.asyncio
async def test_a_transport_failure_is_reported_as_an_integration_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ServiceIntegrationError, match="无法连接邮件服务"):
        await _send(handler)


@pytest.mark.asyncio
async def test_an_oversized_report_is_refused_before_the_request() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"id": "x"})

    huge = RunReportMail(
        run_id=1, subject="big", html="x" * (MAX_BODY_BYTES + 1000)
    )

    with pytest.raises(ServiceIntegrationError, match="未发送"):
        await _send(handler, huge)
    assert calls == []
