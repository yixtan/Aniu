from typing import Annotated

from fastapi import APIRouter, Body, Depends

from backend.api.deps import get_report_mail_service
from backend.api.schemas.error import error_responses
from backend.api.schemas.report_email import (
    ReportEmailSettingsResponse,
    ReportMailResultResponse,
    SaveReportEmailSettingsRequest,
)
from backend.api.security import require_authenticated
from backend.business.reports import ReportMailService, SaveEmailSettingsCommand
from backend.business.reports.commands import UNSET

router = APIRouter(
    prefix="/api/aniu/report-email",
    tags=["Report email"],
    dependencies=[Depends(require_authenticated)],
    responses=error_responses(401, 403, 422),
)


@router.get("", response_model=ReportEmailSettingsResponse)
async def get_report_email_settings(
    service: Annotated[ReportMailService, Depends(get_report_mail_service)],
) -> object:
    return await service.get_settings()


@router.put("", response_model=ReportEmailSettingsResponse)
async def save_report_email_settings(
    service: Annotated[ReportMailService, Depends(get_report_mail_service)],
    payload: SaveReportEmailSettingsRequest = Body(...),
) -> object:
    fields = payload.model_dump(exclude_unset=True)
    return await service.save_settings(
        SaveEmailSettingsCommand(
            sender=fields.get("sender", UNSET),
            recipient=fields.get("recipient", UNSET),
            enabled=fields.get("enabled", UNSET),
            api_key=fields.get("api_key", UNSET),
        )
    )


@router.post(
    "/runs/{run_id}",
    response_model=ReportMailResultResponse,
    responses=error_responses(404, 503),
)
async def email_run_report(
    run_id: int,
    service: Annotated[ReportMailService, Depends(get_report_mail_service)],
) -> object:
    return await service.send_run_report(run_id)
