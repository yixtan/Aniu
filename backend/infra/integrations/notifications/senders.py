"""HTTP transports for each supported push channel."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from backend.business.notifications import (
    NotificationChannel,
    NotificationChannelKind,
    NotificationEvent,
)
from backend.business.shared import ServiceIntegrationError
from backend.infra.integrations.notifications.rendering import (
    MAX_RENDERED_BODY_BYTES,
    default_body,
    render_body_template,
)

DEFAULT_TIMEOUT_SECONDS = 10.0
SERVERCHAN_ENDPOINT = "https://sctapi.ftqq.com/{key}.send"
WECOM_ENDPOINT = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"


def _is_url(secret: str) -> bool:
    return secret.startswith(("http://", "https://"))


def _require_url(secret: str, *, channel_name: str) -> str:
    url = secret.strip()
    if not _is_url(url):
        raise ServiceIntegrationError(
            f"通道 {channel_name} 的地址必须以 http:// 或 https:// 开头"
        )
    return url


def _encoded_body(payload: object, *, channel_name: str) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_RENDERED_BODY_BYTES:
        raise ServiceIntegrationError(
            f"通道 {channel_name} 的推送内容超过 {MAX_RENDERED_BODY_BYTES} 字节"
        )
    return body


async def _post_json(
    client: httpx.AsyncClient,
    url: str,
    payload: object,
    *,
    channel_name: str,
    timeout: float,
) -> httpx.Response:
    try:
        response = await client.post(
            url,
            content=_encoded_body(payload, channel_name=channel_name),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ServiceIntegrationError(
            f"推送失败：HTTP {exc.response.status_code}",
            status_code=exc.response.status_code,
        ) from exc
    except httpx.HTTPError as exc:
        raise ServiceIntegrationError(f"推送失败：{exc}") from exc
    return response


def _json_body(response: httpx.Response) -> dict[str, Any]:
    try:
        parsed = response.json()
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass(slots=True)
class WebhookSender:
    """POST a caller-defined JSON body to any URL."""

    client: httpx.AsyncClient
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    async def send(
        self,
        *,
        channel: NotificationChannel,
        secret: str,
        event: NotificationEvent,
    ) -> None:
        url = _require_url(secret, channel_name=channel.name)
        if channel.body_template is None:
            payload: object = default_body(event)
        else:
            try:
                payload = render_body_template(channel.body_template, event)
            except ValueError as exc:
                raise ServiceIntegrationError(str(exc)) from exc
        await _post_json(
            self.client,
            url,
            payload,
            channel_name=channel.name,
            timeout=self.timeout_seconds,
        )


@dataclass(slots=True)
class ServerChanSender:
    """Server酱 (sctapi.ftqq.com): title plus a Markdown description."""

    client: httpx.AsyncClient
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    async def send(
        self,
        *,
        channel: NotificationChannel,
        secret: str,
        event: NotificationEvent,
    ) -> None:
        key = secret.strip()
        url = key if _is_url(key) else SERVERCHAN_ENDPOINT.format(key=quote(key))
        response = await _post_json(
            self.client,
            url,
            {"title": event.title, "desp": event.as_markdown()},
            channel_name=channel.name,
            timeout=self.timeout_seconds,
        )
        body = _json_body(response)
        code = body.get("code")
        if code is not None and int(code) != 0:
            message = str(body.get("message") or body.get("info") or "未知错误")
            raise ServiceIntegrationError(f"Server酱 拒绝了推送：{message}")


@dataclass(slots=True)
class WeComBotSender:
    """企业微信群机器人 webhook: a Markdown message card."""

    client: httpx.AsyncClient
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    async def send(
        self,
        *,
        channel: NotificationChannel,
        secret: str,
        event: NotificationEvent,
    ) -> None:
        key = secret.strip()
        url = key if _is_url(key) else WECOM_ENDPOINT.format(key=quote(key))
        response = await _post_json(
            self.client,
            url,
            {"msgtype": "markdown", "markdown": {"content": event.as_markdown()}},
            channel_name=channel.name,
            timeout=self.timeout_seconds,
        )
        body = _json_body(response)
        errcode = body.get("errcode")
        if errcode is not None and int(errcode) != 0:
            message = str(body.get("errmsg") or "未知错误")
            raise ServiceIntegrationError(f"企业微信拒绝了推送：{message}")


@dataclass(slots=True)
class RoutingNotificationSender:
    """Pick the transport that matches the channel kind."""

    webhook: WebhookSender
    serverchan: ServerChanSender
    wecom: WeComBotSender

    @classmethod
    def create(
        cls,
        client: httpx.AsyncClient,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> RoutingNotificationSender:
        return cls(
            webhook=WebhookSender(client, timeout_seconds),
            serverchan=ServerChanSender(client, timeout_seconds),
            wecom=WeComBotSender(client, timeout_seconds),
        )

    async def send(
        self,
        *,
        channel: NotificationChannel,
        secret: str,
        event: NotificationEvent,
    ) -> None:
        if channel.kind is NotificationChannelKind.WEBHOOK:
            sender: Any = self.webhook
        elif channel.kind is NotificationChannelKind.SERVERCHAN:
            sender = self.serverchan
        else:
            sender = self.wecom
        await sender.send(channel=channel, secret=secret, event=event)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "RoutingNotificationSender",
    "ServerChanSender",
    "WeComBotSender",
    "WebhookSender",
]
