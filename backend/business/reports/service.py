"""Application service for mailing a completed run report."""

from __future__ import annotations

import logging

from backend.business.reports.commands import SaveEmailSettingsCommand
from backend.business.reports.dto import (
    EmailDeliverySettingsDTO,
    ReportMailResultDTO,
    to_email_settings_dto,
)
from backend.business.reports.email_body import render_report_email
from backend.business.reports.models import (
    EmailDeliverySettings,
    RunReportMail,
    normalize_address,
)
from backend.business.reports.ports import (
    EmailSettingsRepositoryPort,
    ReportMailerPort,
    RunReportQueryPort,
)
from backend.business.shared import (
    CommitterPort,
    RunNotFoundError,
    ServiceConfigurationError,
)

logger = logging.getLogger(__name__)


def _last_four(secret: str) -> str:
    text = secret.strip()
    return text[-4:] if len(text) >= 4 else text


class ReportMailService:
    """Configure where run reports are mailed, and mail one on request."""

    def __init__(
        self,
        *,
        settings_repo: EmailSettingsRepositoryPort,
        run_report_query: RunReportQueryPort,
        mailer: ReportMailerPort,
        committer: CommitterPort | None = None,
    ) -> None:
        self._settings_repo = settings_repo
        self._run_report_query = run_report_query
        self._mailer = mailer
        self._committer = committer

    async def get_settings(self) -> EmailDeliverySettingsDTO:
        return to_email_settings_dto(await self._settings_repo.get())

    async def save_settings(
        self, command: SaveEmailSettingsCommand
    ) -> EmailDeliverySettingsDTO:
        current = await self._settings_repo.get()
        api_key: str | None = None
        if command.provided("api_key"):
            raw = command.api_key
            candidate = "" if raw is None else str(raw).strip()
            # A blank key means "keep the stored one", so the client never has
            # to round-trip a credential it was not shown.
            api_key = candidate or None

        sender = (
            normalize_address(str(command.sender), "sender")
            if command.provided("sender")
            else (current.sender if current else "")
        )
        recipient = (
            normalize_address(str(command.recipient), "recipient")
            if command.provided("recipient")
            else (current.recipient if current else "")
        )
        if not sender or not recipient:
            raise ValueError("sender and recipient are required")
        if current is None and api_key is None:
            raise ValueError("api_key is required when configuring email delivery")

        saved = await self._settings_repo.save(
            EmailDeliverySettings(
                sender=sender,
                recipient=recipient,
                enabled=(
                    bool(command.enabled)
                    if command.provided("enabled")
                    else (current.enabled if current else True)
                ),
                api_key_last_four=(
                    _last_four(api_key)
                    if api_key
                    else (current.api_key_last_four if current else None)
                ),
                created_at=current.created_at if current else EmailDeliverySettings(
                    sender=sender, recipient=recipient
                ).created_at,
            ),
            api_key=api_key,
        )
        await self._commit()
        return to_email_settings_dto(saved)

    async def send_run_report(self, run_id: int) -> ReportMailResultDTO:
        settings = await self._settings_repo.get()
        if settings is None:
            raise ServiceConfigurationError("尚未配置邮件投递，请先在设置中填写。")
        if not settings.enabled:
            raise ServiceConfigurationError("邮件投递已关闭，请先在设置中启用。")
        api_key = await self._settings_repo.get_api_key()
        if not api_key:
            raise ServiceConfigurationError("邮件投递缺少 API Key，请重新填写。")

        report = await self._run_report_query.get_report(run_id)
        if report is None:
            raise RunNotFoundError(run_id)
        summary, render_mode = report
        if not summary.strip():
            raise ServiceConfigurationError("该运行还没有可发送的报告。")

        mail = RunReportMail(
            run_id=run_id,
            subject=f"Aniu 运行报告 · #{run_id}",
            html=render_report_email(summary, render_mode),
        )
        try:
            await self._mailer.send(
                api_key=api_key,
                sender=settings.sender,
                recipient=settings.recipient,
                mail=mail,
            )
        except Exception as exc:
            logger.warning(
                "run report email failed",
                extra={"run_id": run_id},
                exc_info=True,
            )
            return ReportMailResultDTO(
                run_id=run_id,
                delivered=False,
                message=f"发送失败：{exc}",
            )
        return ReportMailResultDTO(
            run_id=run_id,
            delivered=True,
            message=f"报告已发送至 {settings.recipient}",
        )

    async def _commit(self) -> None:
        if self._committer is not None:
            await self._committer.commit()


__all__ = ["ReportMailService"]
