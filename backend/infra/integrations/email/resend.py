"""Resend transport for run report emails."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from backend.business.reports import RunReportMail
from backend.business.shared import ServiceIntegrationError

RESEND_ENDPOINT = "https://api.resend.com/emails"
DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_BODY_BYTES = 2_000_000
"""Well under Resend's own limit; a report far larger than this is a bug."""


@dataclass(slots=True)
class ResendReportMailer:
    client: httpx.AsyncClient
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    async def send(
        self,
        *,
        api_key: str,
        sender: str,
        recipient: str,
        mail: RunReportMail,
    ) -> None:
        body = json.dumps(
            {
                "from": sender,
                "to": [recipient],
                "subject": mail.subject,
                "html": mail.html,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        if len(body) > MAX_BODY_BYTES:
            raise ServiceIntegrationError(
                f"报告内容超过 {MAX_BODY_BYTES // 1000} KB，未发送"
            )
        try:
            response = await self.client.post(
                RESEND_ENDPOINT,
                content=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise ServiceIntegrationError(f"无法连接邮件服务：{exc}") from exc

        if response.status_code >= 400:
            raise ServiceIntegrationError(
                _error_detail(response), status_code=response.status_code
            )


def _error_detail(response: httpx.Response) -> str:
    """Turn a Resend error body into something an operator can act on."""

    payload: Any
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error")
        if isinstance(message, str) and message.strip():
            return f"邮件服务拒绝了请求（HTTP {response.status_code}）：{message}"
    return f"邮件服务拒绝了请求（HTTP {response.status_code}）"


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "RESEND_ENDPOINT", "ResendReportMailer"]
