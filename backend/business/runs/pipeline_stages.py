"""Canonical metadata for the two-stage strategy run."""

from __future__ import annotations

from dataclasses import dataclass

from backend.business.shared.enums import RunState


@dataclass(frozen=True, slots=True)
class PipelineStage:
    run_state: RunState
    stage_id: str
    trace_key: str
    title: str
    description: str

    @property
    def state_name(self) -> str:
        return self.run_state.value


RUN = PipelineStage(
    run_state=RunState.RUN,
    stage_id="Run",
    trace_key="run",
    title="任务执行",
    description="完成研究、判断、交易与 Markdown 运行报告",
)
SUMMARY = PipelineStage(
    run_state=RunState.SUMMARY,
    stage_id="Summary",
    trace_key="summary",
    title="展示总结",
    description="根据运行报告与执行证据生成 HTML 总结",
)

WATCH = PipelineStage(
    run_state=RunState.WATCH,
    stage_id="Watch",
    trace_key="watch",
    title="盯盘",
    description="按运行写下的挂单处置清单逐笔核对并执行",
)

PIPELINE: tuple[PipelineStage, ...] = (RUN, SUMMARY)
"""The analysis run's state machine. The order watch is a run of its own."""

WATCH_PIPELINE: tuple[PipelineStage, ...] = (WATCH,)

# Lookups answer for every stage that can appear in a trace, not just the
# analysis pipeline. Keeping PIPELINE itself to the two-stage FSM is what
# stops a watch from being treated as a step an analysis run must pass.
_ALL_STAGES: tuple[PipelineStage, ...] = (*PIPELINE, *WATCH_PIPELINE)
_BY_TRACE_KEY = {stage.trace_key: stage for stage in _ALL_STAGES}
TRACE_STAGE_META = {
    stage.trace_key: (stage.title, stage.description) for stage in _ALL_STAGES
}


def pipeline_stage_for_state_name(stage_name: str) -> PipelineStage | None:
    if not stage_name:
        return None
    base = stage_name.split(":", 1)[0]
    for stage in _ALL_STAGES:
        if base in {stage.state_name, stage.stage_id}:
            return stage
    return None


def pipeline_stage_for_trace_key(trace_key: str) -> PipelineStage | None:
    return _BY_TRACE_KEY.get(trace_key)


def trace_key_for_stage_name(stage_name: str) -> str:
    stage = pipeline_stage_for_state_name(stage_name)
    if stage is None:
        raise ValueError(f"unknown pipeline stage: {stage_name}")
    return stage.trace_key


def is_tool_capable_stage(stage_name: str) -> bool:
    return pipeline_stage_for_state_name(stage_name) in {RUN, WATCH}


def is_write_stage(stage_name: str) -> bool:
    # A watch cancels, which is a side effect, so it is a write stage. What it
    # may write is decided by the tools it can reach, not by this flag.
    return pipeline_stage_for_state_name(stage_name) in {RUN, WATCH}


def result_title_for_stage(stage_name: str) -> str:
    stage = pipeline_stage_for_state_name(stage_name)
    if stage is RUN:
        return "生成运行报告"
    if stage is WATCH:
        return "生成盯盘记录"
    return "生成 HTML 总结"


def streaming_summary_for_stage(stage_name: str) -> str:
    stage = pipeline_stage_for_state_name(stage_name)
    if stage is RUN:
        return "正在生成运行报告"
    if stage is WATCH:
        return "正在核对挂单"
    return "正在生成 HTML 总结"
