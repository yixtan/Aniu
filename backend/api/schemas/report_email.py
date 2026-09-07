from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.api.schemas.common import ApiModel


class ReportEmailSettingsResponse(ApiModel):
    configured: bool
    enabled: bool
    sender: str
    recipient: str
    api_key_configured: bool
    api_key_last_four: str | None
    created_at: datetime | None
    updated_at: datetime | None


class ReportMailResultResponse(ApiModel):
    run_id: int
    delivered: bool
    message: str


class SaveReportEmailSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender: str | None = Field(default=None, max_length=254)
    """Must be an address the provider has authorised for your account.

    Shape is validated by the domain rather than here, so the rule lives in
    one place and no extra dependency is needed for it.
    """
    recipient: str | None = Field(default=None, max_length=254)
    enabled: bool | None = None
    api_key: str | None = Field(default=None, max_length=200)
    """Leave empty to keep the stored key; clients never receive it back."""
