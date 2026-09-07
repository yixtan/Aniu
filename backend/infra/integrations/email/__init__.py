"""Outbound email transports."""

from backend.infra.integrations.email.resend import ResendReportMailer

__all__ = ["ResendReportMailer"]
