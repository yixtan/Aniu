"""Run report email settings and delivery."""

from __future__ import annotations

import pytest

from backend.business.reports import (
    EmailDeliverySettings,
    ReportMailService,
    RunReportMail,
    SaveEmailSettingsCommand,
    render_report_email,
)
from backend.business.reports.email_body import MAX_CONTENT_WIDTH_PX
from backend.business.shared import RunNotFoundError, ServiceConfigurationError

HTML_REPORT = '<section class="report"><h2>执行总结</h2><p>成交 1 笔</p></section>'
MARKDOWN_REPORT = "## 运行报告\n\n- 买入 600519"


class FakeSettingsRepo:
    def __init__(self, settings: EmailDeliverySettings | None = None) -> None:
        self.settings = settings
        self.api_key = "re_stored_key" if settings else None

    async def get(self) -> EmailDeliverySettings | None:
        return self.settings

    async def save(
        self, settings: EmailDeliverySettings, *, api_key: str | None
    ) -> EmailDeliverySettings:
        self.settings = settings
        if api_key is not None:
            self.api_key = api_key
        return settings

    async def get_api_key(self) -> str | None:
        return self.api_key


class FakeReportQuery:
    def __init__(self, report: tuple[str, str] | None) -> None:
        self.report = report

    async def get_report(self, run_id: int) -> tuple[str, str] | None:
        del run_id
        return self.report


class FakeMailer:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str, RunReportMail]] = []
        self._fail = fail

    async def send(
        self, *, api_key: str, sender: str, recipient: str, mail: RunReportMail
    ) -> None:
        if self._fail:
            raise RuntimeError("邮件服务拒绝了请求（HTTP 403）")
        self.sent.append((api_key, recipient, mail))


def _settings(**overrides: object) -> EmailDeliverySettings:
    payload: dict[str, object] = {
        "sender": "aniu@example.com",
        "recipient": "me@example.com",
        "enabled": True,
        "api_key_last_four": "3fYZ",
    }
    payload.update(overrides)
    return EmailDeliverySettings(**payload)  # type: ignore[arg-type]


def _service(
    settings: EmailDeliverySettings | None = None,
    report: tuple[str, str] | None = (HTML_REPORT, "html"),
    mailer: FakeMailer | None = None,
) -> tuple[ReportMailService, FakeSettingsRepo, FakeMailer]:
    repo = FakeSettingsRepo(settings)
    sender = mailer or FakeMailer()
    return (
        ReportMailService(
            settings_repo=repo,
            run_report_query=FakeReportQuery(report),
            mailer=sender,
        ),
        repo,
        sender,
    )


MIXED_REPORT = """## 一、市场环境

**核心特征：极端分化**。沪指几乎平盘。

<div style="color:#111;">模型自己写的卡片</div>

正文段落。
"""


def test_markdown_is_converted_the_way_the_web_page_converts_it() -> None:
    """The page renders with remark; markdown-it-py follows the same rules."""

    body = render_report_email(MIXED_REPORT, "html")

    assert "<h2" in body and "## 一、市场环境" not in body
    assert "<strong>核心特征：极端分化</strong>" in body
    assert "**" not in body


def test_the_report_own_inline_markup_is_left_untouched() -> None:
    body = render_report_email(MIXED_REPORT, "html")

    assert '<div style="color:#111;">模型自己写的卡片</div>' in body


def test_generated_tags_get_inline_styles_because_clients_strip_css() -> None:
    body = render_report_email(MIXED_REPORT, "html")

    assert "<h2 style=" in body
    assert "<p style=" in body


def test_the_body_is_width_constrained() -> None:
    """Mail clients render at window width unless a container says otherwise."""

    assert f"max-width:{MAX_CONTENT_WIDTH_PX}px" in render_report_email(
        HTML_REPORT, "html"
    )


def test_a_markdown_report_is_escaped_into_a_readable_block() -> None:
    """A degraded run yields Markdown, which no mail client renders as markup."""

    body = render_report_email("# 标题 <script>alert(1)</script>", "markdown")

    assert "<pre" in body
    assert "&lt;script&gt;" in body
    assert "<script>" not in body


@pytest.mark.asyncio
async def test_sending_uses_the_stored_key_and_recipient() -> None:
    service, _, mailer = _service(_settings())

    result = await service.send_run_report(42)

    assert result.delivered is True
    assert "me@example.com" in result.message
    api_key, recipient, mail = mailer.sent[0]
    assert api_key == "re_stored_key"
    assert recipient == "me@example.com"
    assert mail.subject == "Aniu 运行报告 · #42"
    # Bare tags from the report gain inline styles, so match on content.
    assert "执行总结" in mail.html
    assert "成交 1 笔" in mail.html
    assert f"max-width:{MAX_CONTENT_WIDTH_PX}px" in mail.html


@pytest.mark.asyncio
async def test_a_refused_delivery_is_reported_not_raised() -> None:
    service, _, _ = _service(_settings(), mailer=FakeMailer(fail=True))

    result = await service.send_run_report(42)

    assert result.delivered is False
    assert "HTTP 403" in result.message


@pytest.mark.asyncio
async def test_sending_without_configuration_asks_for_it() -> None:
    service, _, _ = _service(None)

    with pytest.raises(ServiceConfigurationError, match="尚未配置"):
        await service.send_run_report(42)


@pytest.mark.asyncio
async def test_a_disabled_configuration_does_not_send() -> None:
    service, _, mailer = _service(_settings(enabled=False))

    with pytest.raises(ServiceConfigurationError, match="已关闭"):
        await service.send_run_report(42)
    assert mailer.sent == []


@pytest.mark.asyncio
async def test_a_run_without_a_report_is_not_found() -> None:
    service, _, _ = _service(_settings(), report=None)

    with pytest.raises(RunNotFoundError):
        await service.send_run_report(42)


@pytest.mark.asyncio
async def test_saving_keeps_the_stored_key_when_the_field_is_blank() -> None:
    service, repo, _ = _service(_settings())

    dto = await service.save_settings(
        SaveEmailSettingsCommand(recipient="other@example.com", api_key="")
    )

    assert repo.api_key == "re_stored_key"
    assert dto.recipient == "other@example.com"
    assert dto.api_key_last_four == "3fYZ"


@pytest.mark.asyncio
async def test_first_configuration_requires_a_key() -> None:
    service, _, _ = _service(None)

    with pytest.raises(ValueError, match="api_key"):
        await service.save_settings(
            SaveEmailSettingsCommand(
                sender="aniu@example.com", recipient="me@example.com"
            )
        )


@pytest.mark.asyncio
async def test_a_new_key_replaces_the_stored_one_and_its_hint() -> None:
    service, repo, _ = _service(_settings())

    dto = await service.save_settings(SaveEmailSettingsCommand(api_key="re_new_ABCD"))

    assert repo.api_key == "re_new_ABCD"
    assert dto.api_key_last_four == "ABCD"


@pytest.mark.parametrize("address", ["not-an-email", "a@b", "", "   "])
def test_malformed_addresses_are_rejected(address: str) -> None:
    with pytest.raises(ValueError):
        EmailDeliverySettings(sender=address, recipient="me@example.com")
