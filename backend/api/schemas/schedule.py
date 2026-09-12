"""Schedule API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.schemas.common import ApiModel
from backend.business.schedules import cadence_for


class StrategyScheduleResponse(ApiModel):
    schedule_id: int
    enabled: bool
    task_type: str
    interval_minutes: int
    custom_schedule_times: list[str] | None = None
    schedule_times: list[str]
    revision: int
    runtime_synced_revision: int
    sync_error: str | None = None
    updated_at: datetime


class SaveScheduleFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Spelled out rather than derived so it reaches OpenAPI as an enum and the
    # generated frontend types keep the union; a test pins it against the
    # domain's own set so the two cannot drift apart.
    task_type: Literal["market_analysis", "order_watch"]
    interval_minutes: int = Field(ge=1)
    schedule_times: list[str] | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _check_cadence(self) -> Self:
        """Apply the floor at the boundary, read from the domain not restated.

        The floor differs per kind, and two copies of a rule that differs per
        kind is how they come to disagree.
        """

        minimum = cadence_for(self.task_type).min_interval_minutes
        if self.interval_minutes < minimum:
            raise ValueError(f"interval_minutes must be >= {minimum}")
        return self


class CreateScheduleRequest(SaveScheduleFields):
    """Create one scheduled task."""


class UpdateScheduleRequest(SaveScheduleFields):
    expected_revision: int = Field(ge=0)
