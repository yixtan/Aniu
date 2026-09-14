"""Run API response schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.api.schemas.common import ApiModel
from backend.business.shared import StockApiProvider

# Every stage the domain can put in a trace. A key missing here is not a
# cosmetic gap: `response_model` validates on the way out, so a run whose
# trace names an unlisted stage cannot be fetched at all. A test pins this
# against TRACE_STAGE_META so the two cannot drift again.
TraceStageKey = Literal["run", "summary", "watch"]
TraceStageStatus = Literal[
    "pending", "running", "completed", "degraded", "failed", "skipped"
]
TraceStepStatus = Literal["pending", "running", "completed", "failed", "blocked"]
TraceStepType = Literal["thinking", "tool", "result", "status"]
TraceToolSource = Literal["aggregate", "mx", "public", "internal"]


class TraceStockApiCallResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    call_id: str
    provider: StockApiProvider
    interface_name: str
    interface_identifier: str
    operation_id: str
    parameters: object | None
    response_characters: int | None
    status: str
    duration_ms: int
    error_message: str | None


class TraceToolCallResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    call_id: str
    intent_line: str
    source: TraceToolSource
    tool_name: str
    display_name: str
    query_parameters: str | None
    model_content_characters: int | None = Field(default=None, ge=0)
    stock_api_calls: list[TraceStockApiCallResponse] | None = None


class TraceStepResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    step_id: str
    type: TraceStepType
    title: str
    status: TraceStepStatus
    summary: str | None
    content: str | None
    tool_call: TraceToolCallResponse | None
    started_at: datetime | None
    ended_at: datetime | None


class TraceStageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    stage_id: str
    key: TraceStageKey
    status: TraceStageStatus
    started_at: datetime | None
    ended_at: datetime | None
    steps: list[TraceStepResponse]


class RunTraceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    schema_version: Literal[3]
    event_seq: int = Field(ge=0)
    current_stage_id: str | None
    stages: list[TraceStageResponse]


class RunDayResponse(ApiModel):
    """One day that produced runs, counted by task."""

    day: date
    analysis_total: int
    analysis_failed: int
    watch_total: int
    watch_failed: int


class RunSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    run_id: int
    task_id: int
    trigger_source: str
    schedule_id: int | None = None
    status: str
    current_state: str
    summary: str | None = None
    summary_render_mode: Literal["markdown", "html"] = "markdown"
    started_at: datetime
    completed_at: datetime | None = None
    tool_calls_count: int = 0
    thinking_count: int = 0
    total_tokens: int = 0
    trade_count: int = 0


class RunDetailResponse(RunSummaryResponse):
    failure_reason: str | None = None
    trace: RunTraceResponse


class AbortRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: int
    status: str
