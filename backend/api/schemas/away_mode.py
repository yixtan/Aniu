from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from backend.api.schemas.common import ApiModel


class AwayModeResponse(ApiModel):
    enabled: bool
    """Derived from ``active_date``, not stored: away mode expires at midnight."""

    active_date: date | None
    updated_at: datetime


class SetAwayModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
