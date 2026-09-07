"""Run report email delivery."""

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
from backend.business.reports.service import ReportMailService

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
    "render_report_email",
    "to_email_settings_dto",
]
