"""Invoke trusted system policy even when the watch would skip the model."""

from dataclasses import asdict, replace

from backend.business.order_directives.valuation_cancellation import (
    ValuationCancellationReport,
)
from backend.business.runs.execution import RunExecutionContext
from backend.business.runs.run_events import RunEventType
from backend.business.runs.stages.stage_helpers import emit_tool_loop_event
from backend.business.shared import RunAbortError
from backend.llm import await_with_abort


async def check_valuation_cancellations(
    context: RunExecutionContext,
    stage: str,
    previous: ValuationCancellationReport | None = None,
) -> ValuationCancellationReport | None:
    if context.valuation_guard is None:
        return None
    if context.abort_signal is not None:
        context.abort_signal.throw_if_aborted()
    try:
        report = await await_with_abort(
            context.valuation_guard(
                context.run.run_id, context.abort_signal, context.cancellations_accepted
            ),
            context.abort_signal,
        )
    except RunAbortError:
        raise
    except Exception:
        report = ValuationCancellationReport(
            issues=("估值撤单检查未完成，不能据此认定已撤销；后续只按持久化状态核对。",)
        )
    if previous is not None:
        by_id = {item.order_id: item for item in previous.items}
        by_id.update({item.order_id: item for item in report.items})
        report = replace(
            report,
            items=tuple(by_id.values()),
            issues=tuple(dict.fromkeys(previous.issues + report.issues)),
        )
    await emit_tool_loop_event(
        context,
        stage_name=stage,
        event_type=RunEventType.VALUATION_CANCELLATIONS_CHECKED,
        payload={**asdict(report), "report_markdown": report.markdown()},
    )
    return report
