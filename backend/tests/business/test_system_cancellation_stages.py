"""System policy precedes HOLD/no-model shortcuts and survives rendering."""

from unittest.mock import AsyncMock

from backend.business.order_directives import DirectiveAction
from backend.business.order_directives.valuation_cancellation import (
    CancellationObservation,
    ValuationCancellationReport,
)
from backend.business.runs.agent_runner import AgentStageResult
from backend.business.runs.stages.run_stage import RunStage
from backend.business.runs.stages.watch_stage import WatchStage
from backend.tests.business.test_run_stage import RecordingRunner, _context
from backend.tests.business.test_watch_stage_execution import (
    _context as watch_context,
)
from backend.tests.business.test_watch_stage_execution import (
    _directive,
)


def system_report(state="SUBMITTED"):
    return ValuationCancellationReport(
        items=(
            CancellationObservation(
                "100000001",
                "600000",
                "测试公司",
                state,
                "VALUATION_FAILED",
                "等待账户确认",
                0,
                "2026-10-29T06:30:00+00:00",
            ),
        )
    )


async def test_no_plan_watch_checks_system_policy_without_invoking_model():
    context = watch_context()
    context.valuation_guard = AsyncMock(return_value=system_report())
    runner = AsyncMock()
    report = await WatchStage().execute(context, runner, directives=())
    context.valuation_guard.assert_awaited_once()
    runner.prompt.assert_not_awaited()
    assert "尚未确认撤销" in report.content


async def test_hold_does_not_override_system_cancellation():
    context = watch_context()
    context.valuation_guard = AsyncMock(return_value=system_report())
    runner = AsyncMock()
    report = await WatchStage().execute(
        context,
        runner,
        directives=(_directive(order_id="100000001", action=DirectiveAction.HOLD),),
    )
    assert "系统生成" in report.content
    assert "尚未确认撤销" in report.content
    runner.prompt.assert_not_awaited()


async def test_run_checks_before_and_after_model_and_retains_confirmed_action():
    context = _context(market_open=True)
    context.valuation_guard = AsyncMock(
        side_effect=[system_report("CANCELLED"), ValuationCancellationReport()]
    )
    runner = RecordingRunner(AgentStageResult(content="模型倾向继续持有。"))
    report = await RunStage().execute(context, runner)
    assert context.valuation_guard.await_count == 2
    assert "system_valuation_cancellations" in runner.prompts[0]
    assert "账户已确认撤销" in report.content
    assert "系统生成" not in report.model_content
    assert report.system_cancellations in report.content


async def test_summary_appends_system_cancellation_unchanged_after_model_render():
    from backend.business.runs.execution import RunReport
    from backend.business.runs.stages.summary_stage import SummaryStage
    from backend.tests.business.test_summary_stage import (
        RecordingRunner as SummaryRunner,
    )
    from backend.tests.business.test_summary_stage import (
        _context as summary_context,
    )

    text = system_report().markdown()
    report = RunReport(
        content="分析" + text, model_content="分析", system_cancellations=text
    )
    runner = SummaryRunner("<p>分析</p>")
    result = await SummaryStage().execute(summary_context(report), runner)
    assert "撤单请求已受理" not in runner.prompts[0]
    assert result.summary == "<p>分析</p>" + text


async def test_system_only_watch_works_without_llm_runtime_or_runner():
    context = watch_context()
    context.llm_runtime = None
    context.valuation_guard = AsyncMock(return_value=system_report())
    report = await WatchStage().execute(context, None, directives=())
    assert "尚未确认撤销" in report.content
    context.valuation_guard.assert_awaited_once()
