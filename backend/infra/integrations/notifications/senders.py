"""HTTP transports for each supported push channel."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from backend.business.notifications import (
    NotificationChannel,
    NotificationChannelKind,
    NotificationEvent,
    NotificationEventKind,
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
DESKTOP_TIMEOUT_SECONDS = 5.0
MAX_DESKTOP_MESSAGE_CHARS = 180
# macOS refuses notifications to an ad-hoc signed helper until the bundle is
# registered once. Brew installs terminal-notifier that way, so this is the
# first thing a new machine hits; the fix is two commands, not a code change.
DESKTOP_PERMISSION_HINT = (
    "macOS 尚未允许该工具发送通知。先注册一次它的 app 包再重试："
    "lsregister -f $(brew --prefix)/opt/terminal-notifier/terminal-notifier.app"
    "，然后 open 同一路径。"
)


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



def _desktop_message(event: NotificationEvent) -> str:
    """One compact line, because a banner truncates anything longer.

    Fields the subtitle already carries are left out, as is the timestamp that
    macOS prints beside every notification — a banner has room for about two
    lines, and repeating what is already on screen spends them.
    """

    redundant = {"事件", "方向", "时间"}
    if event.kind is NotificationEventKind.RUN_FAILED:
        redundant.add("运行")
    parts = [
        f"{label} {value}"
        for label, value in event.as_lines()
        if label not in redundant
    ]
    message = " · ".join(parts) or event.kind.label
    if len(message) > MAX_DESKTOP_MESSAGE_CHARS:
        return message[: MAX_DESKTOP_MESSAGE_CHARS - 1] + "…"
    return message


async def _run_command(argv: list[str], *, channel_name: str) -> tuple[int, str]:
    """Run a local notifier. No shell, so content is never parsed as commands."""

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise ServiceIntegrationError(
            f"通道 {channel_name} 无法启动 {argv[0]}：{exc}"
        ) from exc
    try:
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=DESKTOP_TIMEOUT_SECONDS
        )
    except TimeoutError as exc:
        process.kill()
        raise ServiceIntegrationError(
            f"通道 {channel_name} 的桌面通知超时"
        ) from exc
    return process.returncode or 0, stderr.decode("utf-8", "replace").strip()


@dataclass(slots=True)
class MacDesktopSender:
    """macOS Notification Center, through whichever local tool is installed.

    `terminal-notifier` is preferred because its notifications carry a click
    target and replace one another within a group — a day of order pushes then
    leaves one banner instead of fifteen. `osascript` is the fallback: it still
    shows the banner, but macOS credits the notification to Script Editor, so
    clicking it opens that app rather than Aniu.
    """

    async def send(
        self,
        *,
        channel: NotificationChannel,
        secret: str,
        event: NotificationEvent,
    ) -> None:
        if sys.platform != "darwin":
            raise ServiceIntegrationError(
                f"通道 {channel.name} 只能在 macOS 主机上发送桌面通知"
            )
        subtitle = event.title.removeprefix("Aniu ")
        message = _desktop_message(event)
        notifier = shutil.which("terminal-notifier")
        if notifier is not None:
            argv = [
                notifier,
                "-title",
                "Aniu",
                "-subtitle",
                subtitle,
                "-message",
                message,
                # Grouped by event kind rather than globally: repeated order
                # pushes collapse into one, while a run failure keeps its own
                # banner instead of being replaced by the next fill.
                "-group",
                f"aniu-{event.kind.value}",
            ]
            target = secret.strip()
            if _is_url(target):
                argv += ["-open", target]
            code, stderr = await _run_command(argv, channel_name=channel.name)
            if code != 0:
                detail = stderr or f"terminal-notifier 退出码 {code}"
                if "not allowed" in stderr.lower():
                    detail = f"{detail}。{DESKTOP_PERMISSION_HINT}"
                raise ServiceIntegrationError(
                    f"通道 {channel.name} 发送失败：{detail}"
                )
            return

        # Arguments are passed as argv, never interpolated into the script text,
        # so a quote in a stock name or an error message cannot alter the script.
        script = (
            "on run argv\n"
            "display notification (item 1 of argv) "
            "with title (item 2 of argv) subtitle (item 3 of argv)\n"
            "end run"
        )
        code, stderr = await _run_command(
            ["osascript", "-e", script, message, "Aniu", subtitle],
            channel_name=channel.name,
        )
        if code != 0:
            raise ServiceIntegrationError(
                f"通道 {channel.name} 发送失败：{stderr or f'osascript 退出码 {code}'}"
            )


@dataclass(slots=True)
class RoutingNotificationSender:
    """Pick the transport that matches the channel kind."""

    webhook: WebhookSender
    serverchan: ServerChanSender
    wecom: WeComBotSender
    macos_desktop: MacDesktopSender

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
            macos_desktop=MacDesktopSender(),
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
        elif channel.kind is NotificationChannelKind.MACOS_DESKTOP:
            sender = self.macos_desktop
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
