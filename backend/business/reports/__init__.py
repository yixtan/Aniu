"""Run report email delivery."""

from backend.business.reports.commands import SaveEmailSettingsCommand
from backend.business.reports.dto import (
    EmailDeliverySettingsDTO,
    ReportMailResultDTO,
    to_email_settings_dto,
)
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
from backend.business.reports.service import ReportMailService, render_report_html

__all__ = [
    "EmailDeliverySettings",
    "EmailDeliverySettingsDTO",
    "EmailSettingsRepositoryPort",
    "ReportMailResultDTO",
    "ReportMailService",
    "ReportMailerPort",
    "RunReportMail",
    "RunReportQueryPort",
    "SaveEmailSettingsCommand",
    "normalize_address",
    "render_report_html",
    "to_email_settings_dto",
]
