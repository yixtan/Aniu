"""HTTP behaviour of each push transport."""

from __future__ import annotations

import json

import httpx
import pytest

from backend.business.notifications import (
    NotificationChannel,
    NotificationChannelKind,
    NotificationEvent,
    NotificationEventKind,
    TradeDirection,
)
from backend.business.shared import ServiceIntegrationError
from backend.infra.integrations.notifications import (
    RoutingNotificationSender,
    ServerChanSender,
    WebhookSender,
    WeComBotSender,
    render_body_template,
    senders,
)

EVENT = NotificationEvent(
    kind=NotificationEventKind.ORDER_PLACED,
    run_id=12,
    stage_name="Run",
    instruction="买入 600519 1700 100",
    direction=TradeDirection.BUY,
    stock_code="600519",
    stock_name="贵州茅台",
    price=1700.0,
    quantity=100,
    order_id="26215460",
)


def _channel(
    kind: NotificationChannelKind = NotificationChannelKind.WEBHOOK,
    *,
    body_template: str | None = None,
) -> NotificationChannel:
    return NotificationChannel(
        id=1, name="测试通道", kind=kind, body_template=body_template
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_webhook_posts_the_full_event_by_default() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as client:
        await WebhookSender(client).send(
            channel=_channel(), secret="https://hook.test/aniu", event=EVENT
        )

    assert captured["url"] == "https://hook.test/aniu"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["event"] == "order_placed"
    assert body["order_id"] == "26215460"
    assert body["stock_code"] == "600519"


@pytest.mark.asyncio
async def test_webhook_renders_a_custom_body_template() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200)

    template = '{"msgtype":"text","text":{"content":"{{title}}"}}'
    async with _client(handler) as client:
        await WebhookSender(client).send(
            channel=_channel(body_template=template),
            secret="https://hook.test/feishu",
            event=EVENT,
        )

    assert captured["body"] == {
        "msgtype": "text",
        "text": {"content": "Aniu 已下单 · 买入 600519 贵州茅台"},
    }


def test_multiline_values_stay_valid_json_after_substitution() -> None:
    rendered = render_body_template('{"content":"{{text}}"}', EVENT)

    assert isinstance(rendered, dict)
    assert "\n" in str(rendered["content"])


def test_unknown_template_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown notification template field"):
        render_body_template('{"a":"{{nope}}"}', EVENT)


def test_template_that_does_not_render_json_is_rejected() -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        render_body_template('{"a": {{title}}}', EVENT)


@pytest.mark.asyncio
async def test_webhook_rejects_a_non_http_endpoint() -> None:
    async with _client(lambda request: httpx.Response(200)) as client:
        with pytest.raises(ServiceIntegrationError, match="http"):
            await WebhookSender(client).send(
                channel=_channel(), secret="ftp://hook.test", event=EVENT
            )


@pytest.mark.asyncio
async def test_webhook_surfaces_an_http_error_status() -> None:
    async with _client(lambda request: httpx.Response(500)) as client:
        with pytest.raises(ServiceIntegrationError, match="HTTP 500"):
            await WebhookSender(client).send(
                channel=_channel(), secret="https://hook.test/x", event=EVENT
            )


@pytest.mark.asyncio
async def test_serverchan_builds_its_endpoint_from_a_sendkey() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0})

    async with _client(handler) as client:
        await ServerChanSender(client).send(
            channel=_channel(NotificationChannelKind.SERVERCHAN),
            secret="SCT123abc",
            event=EVENT,
        )

    assert captured["url"] == "https://sctapi.ftqq.com/SCT123abc.send"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["title"] == "Aniu 已下单 · 买入 600519 贵州茅台"
    assert "贵州茅台" in str(body["desp"])


@pytest.mark.asyncio
async def test_serverchan_reports_a_business_level_rejection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 40001, "message": "bad sendkey"})

    async with _client(handler) as client:
        with pytest.raises(ServiceIntegrationError, match="bad sendkey"):
            await ServerChanSender(client).send(
                channel=_channel(NotificationChannelKind.SERVERCHAN),
                secret="SCT123abc",
                event=EVENT,
            )


@pytest.mark.asyncio
async def test_wecom_posts_markdown_and_builds_its_endpoint_from_a_key() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    async with _client(handler) as client:
        await WeComBotSender(client).send(
            channel=_channel(NotificationChannelKind.WECOM_BOT),
            secret="key-9876",
            event=EVENT,
        )

    assert "qyapi.weixin.qq.com" in str(captured["url"])
    assert "key=key-9876" in str(captured["url"])
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["msgtype"] == "markdown"


@pytest.mark.asyncio
async def test_wecom_reports_a_business_level_rejection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errcode": 93000, "errmsg": "invalid webhook"})

    async with _client(handler) as client:
        with pytest.raises(ServiceIntegrationError, match="invalid webhook"):
            await WeComBotSender(client).send(
                channel=_channel(NotificationChannelKind.WECOM_BOT),
                secret="key-9876",
                event=EVENT,
            )


@pytest.mark.asyncio
async def test_a_full_url_secret_is_used_verbatim_for_vendor_channels() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"errcode": 0})

    async with _client(handler) as client:
        await WeComBotSender(client).send(
            channel=_channel(NotificationChannelKind.WECOM_BOT),
            secret="https://proxy.internal/wecom?key=abc",
            event=EVENT,
        )

    assert captured["url"] == "https://proxy.internal/wecom?key=abc"


@pytest.mark.asyncio
async def test_router_picks_the_transport_matching_the_channel_kind() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"code": 0, "errcode": 0})

    async with _client(handler) as client:
        router = RoutingNotificationSender.create(client)
        for kind, secret in (
            (NotificationChannelKind.WEBHOOK, "https://hook.test/a"),
            (NotificationChannelKind.SERVERCHAN, "SCTkey"),
            (NotificationChannelKind.WECOM_BOT, "botkey"),
        ):
            await router.send(channel=_channel(kind), secret=secret, event=EVENT)

    assert seen == [
        "https://hook.test/a",
        "https://sctapi.ftqq.com/SCTkey.send",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=botkey",
    ]


class _FakeProcess:
    def __init__(self, returncode: int, stderr: bytes) -> None:
        self.returncode = returncode
        self._stderr = stderr
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr

    def kill(self) -> None:
        self.killed = True


def _record_commands(
    monkeypatch: pytest.MonkeyPatch,
    *,
    notifier: str | None,
    returncode: int = 0,
    stderr: bytes = b"",
) -> list[list[str]]:
    """Capture the argv a sender would run instead of running it."""

    calls: list[list[str]] = []

    async def fake_exec(*argv: str, **_: object) -> _FakeProcess:
        calls.append(list(argv))
        return _FakeProcess(returncode, stderr)

    monkeypatch.setattr(senders.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(senders.shutil, "which", lambda _name: notifier)
    monkeypatch.setattr(senders.sys, "platform", "darwin")
    return calls


@pytest.mark.asyncio
async def test_terminal_notifier_gets_a_click_target_and_a_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record_commands(
        monkeypatch, notifier="/opt/homebrew/bin/terminal-notifier"
    )

    await senders.MacDesktopSender().send(
        channel=_channel(kind=NotificationChannelKind.MACOS_DESKTOP),
        secret="http://localhost:5173/",
        event=EVENT,
    )

    argv = calls[0]
    assert argv[0] == "/opt/homebrew/bin/terminal-notifier"
    assert argv[argv.index("-open") + 1] == "http://localhost:5173/"
    # Grouped per event kind, so repeated order pushes collapse into one while a
    # run failure keeps its own banner.
    assert argv[argv.index("-group") + 1] == "aniu-order_placed"
    assert argv[argv.index("-title") + 1] == "Aniu"
    assert "已下单" in argv[argv.index("-subtitle") + 1]


@pytest.mark.asyncio
async def test_a_run_failure_lands_in_its_own_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record_commands(monkeypatch, notifier="/usr/local/bin/terminal-notifier")

    await senders.MacDesktopSender().send(
        channel=_channel(kind=NotificationChannelKind.MACOS_DESKTOP),
        secret="http://localhost:5173/",
        event=NotificationEvent(
            kind=NotificationEventKind.RUN_FAILED, run_id=9, failure_reason="超时"
        ),
    )

    argv = calls[0]
    assert argv[argv.index("-group") + 1] == "aniu-run_failed"


@pytest.mark.asyncio
async def test_an_address_that_is_not_a_url_leaves_the_banner_unclickable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record_commands(monkeypatch, notifier="/usr/local/bin/terminal-notifier")

    await senders.MacDesktopSender().send(
        channel=_channel(kind=NotificationChannelKind.MACOS_DESKTOP),
        secret="不是地址",
        event=EVENT,
    )

    assert "-open" not in calls[0]


@pytest.mark.asyncio
async def test_without_terminal_notifier_it_falls_back_to_osascript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record_commands(monkeypatch, notifier=None)

    await senders.MacDesktopSender().send(
        channel=_channel(kind=NotificationChannelKind.MACOS_DESKTOP),
        secret="http://localhost:5173/",
        event=EVENT,
    )

    argv = calls[0]
    assert argv[0] == "osascript"
    # Content travels as argv, never inside the script text, so a quote in a
    # stock name or an error message cannot rewrite the script.
    assert argv[1] == "-e"
    assert "600519" not in argv[2]
    assert any("600519" in part for part in argv[3:])


@pytest.mark.asyncio
async def test_content_with_quotes_stays_an_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record_commands(monkeypatch, notifier=None)
    hostile = 'x" with title "劫持" ignoring "'

    await senders.MacDesktopSender().send(
        channel=_channel(kind=NotificationChannelKind.MACOS_DESKTOP),
        secret="",
        event=NotificationEvent(
            kind=NotificationEventKind.RUN_FAILED, run_id=3, failure_reason=hostile
        ),
    )

    argv = calls[0]
    assert "劫持" not in argv[2]
    assert any(hostile in part for part in argv[3:])


@pytest.mark.asyncio
async def test_a_blocked_notification_says_how_to_unblock_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first thing a new Mac hits, and the fix is not in this codebase."""

    _record_commands(
        monkeypatch,
        notifier="/usr/local/bin/terminal-notifier",
        returncode=3,
        stderr=b"Could not request notification permission: Notifications are "
        b"not allowed for this application",
    )

    with pytest.raises(ServiceIntegrationError) as caught:
        await senders.MacDesktopSender().send(
            channel=_channel(kind=NotificationChannelKind.MACOS_DESKTOP),
            secret="http://localhost:5173/",
            event=EVENT,
        )

    assert "lsregister" in str(caught.value)


@pytest.mark.asyncio
async def test_it_refuses_to_pretend_on_a_non_mac_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(senders.sys, "platform", "linux")

    with pytest.raises(ServiceIntegrationError, match="macOS"):
        await senders.MacDesktopSender().send(
            channel=_channel(kind=NotificationChannelKind.MACOS_DESKTOP),
            secret="http://localhost:5173/",
            event=EVENT,
        )
