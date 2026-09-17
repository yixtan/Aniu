"""The two halves of an independent review, on the business-neutral harness.

Neither half goes through `settings_for_stage`. That composes the global
prompt in front of any stage it is asked about, and the global prompt carries
this account's own framing — the reviewer must not be handed the standard it
is supposed to judge against. For the same reason the critic is given no
memory: a critic that reads the rules the run wrote for itself measures the
run against them and returns a rubber stamp.

Neither half gets a tool. Asked for a number it does not hold, the model is
told to name the call it would make instead of reaching for a proxy — which is
what it did when this was tried against the live provider, while every attempt
to reason from a proxy produced a confident and wrong discrepancy.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from backend.agent.harness import AgentHarness
from backend.business.evaluations import EvaluationResult, EvaluatorPort
from backend.business.fill_record import FillRecord
from backend.business.runs.reports import RunReportRecord
from backend.business.settings import AppSettings
from backend.business.settings.ports import SettingsRepositoryPort
from backend.infra.integrations.agent_runtime import AgentRuntimeFactory
from backend.llm import LLMClientPort

CRITIC_PROMPT = """你是一位独立的第三方评估者，受托审查一个自动交易账户的单次运行质量。

你不属于这个系统，也不知道它内部有哪些规则、记忆或提示词。你只看得到两样东西：这次运行自己写的报告，以及这个账户的委托成交记录。

委托成交记录直接来自该账户的数据库，是权威事实。报告与它冲突时，以记录为准。

你的任务是提出 3 到 5 个高水平的问题，交给这次运行的执行者回答。

要求：
1. 每个问题必须能被证据回答或推翻。写不出「什么结果会说明你错了」的问题，不要提。
2. 优先提那些只有对照历史记录才能看出来的问题，而不是复述报告里已经写明的内容。
3. 不要提程序运维、接口调用、重试之类的技术执行问题，只谈投资判断与风险。
4. 每个问题配一句话说明你为什么问它，依据是哪个具体数字。
5. 按重要性排序，最重要的放第一个。

用中文回答。不要写客套话和总结段落。"""

ANSWER_PROMPT = """你是这次运行的执行者，现在要回答一位独立第三方评估者对你的提问。

你手上有：你自己写的运行报告，以及提问者引用的委托成交记录。记录直接来自账户数据库，是权威事实，不是提问者的声称。

规则：
1. 不许编造数字。凡是你手上没有的数据，明确说「这个数我手上没有，需要调用 X 工具查 Y」，
   而不是用手边的数去近似、推算或替代。
2. 如果一个问题的前提与你的事实不符，先指出前提错在哪，再回答它真正想问的部分。
3. 承认错误就直接承认，不要绕。
4. 每个问题分开回答，按提问顺序。

用中文回答。不要客套话。"""

MISSING_REPORT = "（这次运行没有留下 Markdown 报告。）"

ReportReader = Callable[[int], Awaitable[RunReportRecord | None]]
FillRecordReader = Callable[[int], Awaitable[FillRecord]]


def render_fill_record(record: FillRecord) -> str:
    """The record as the reviewer reads it: this run's own orders, then the trend.

    Two blocks, never one. Handing over a single per-day table is what let a
    reviewer read a day's total as one run's own orders and then demand the
    difference be explained; there was no difference, only other runs.
    """

    lines = [f"## 本次运行（{record.run_id}）自己下的委托", ""]
    if record.own_orders:
        lines += [
            "| 标的 | 方向 | 价格 × 数量 | 此刻状态 |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| {order.symbol} {order.stock_name} | {order.direction} | "
            f"{order.order_price} × {order.quantity} | {order.status} |"
            for order in record.own_orders
        ]
        lines += [
            "",
            f"本次共 {len(record.own_orders)} 笔，成交 {record.own_filled} 笔。"
            "状态是此刻的状态，不是下单当时的；标着 CANCELLED 的"
            "可能是本次运行自己撤的，也可能是后来的运行撤的。",
        ]
    else:
        lines.append("本次运行没有下单。")
    lines += ["", "## 账户开户至今，按日汇总（这一天全部运行的委托合计）", ""]
    lines += [
        "| 日期 | 下单的运行数 | 委托 | 成交 | 成交率 |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {day.day} | {day.placing_runs} | {day.ordered} | {day.filled} | "
        f"{day.fill_rate:.0%} |"
        for day in record.days
    ]
    if record.unattributed:
        lines += [
            "",
            f"其中 {record.unattributed} 笔早期委托归属不明"
            "（下单记录早于工具流水留存），"
            "已计入按日汇总，未计入任何一次运行。",
        ]
    return "\n".join(lines)


@dataclass(slots=True)
class EvaluationAgent(EvaluatorPort):
    llm_client: LLMClientPort
    runtime_factory: AgentRuntimeFactory
    settings_repo: SettingsRepositoryPort
    context_reader: EvaluationContextReader

    async def evaluate(self, run_id: int) -> EvaluationResult:
        report, record = await self.context_reader.read(run_id)
        settings = await self.settings_repo.get() or AppSettings()
        # The reviewer borrows the Run stage's model settings and none of its
        # prompt, so changing the trading model moves its reviewer with it.
        runtime = await self.runtime_factory.build_stage_runtime(
            settings.stage_settings["Run"]
        )
        rendered = render_fill_record(record)
        body = report.content if report is not None else MISSING_REPORT

        critic = AgentHarness(
            runtime=runtime,
            llm_client=self.llm_client,
            system_prompt=CRITIC_PROMPT,
            label="Evaluate",
        )
        asked = await critic.prompt(f"## 这次运行的报告\n\n{body}\n\n{rendered}")
        questions = asked.content.strip()

        answerer = AgentHarness(
            runtime=runtime,
            llm_client=self.llm_client,
            system_prompt=ANSWER_PROMPT,
            label="Answer",
        )
        answered = await answerer.prompt(
            f"## 我的运行报告\n\n{body}\n\n{rendered}\n\n## 评估者的提问\n\n{questions}"
        )
        return EvaluationResult(
            questions=questions,
            answers=answered.content.strip(),
            total_tokens=asked.usage.total_tokens + answered.usage.total_tokens,
            cached_tokens=asked.usage.cache_read + answered.usage.cache_read,
        )


class EvaluationContextReader:
    """Everything a review reads, gathered in one place so it can be tested."""

    def __init__(
        self,
        *,
        report_for_run: ReportReader,
        fill_record_for_run: FillRecordReader,
    ) -> None:
        self._report_for_run = report_for_run
        self._fill_record_for_run = fill_record_for_run

    async def read(self, run_id: int) -> tuple[RunReportRecord | None, FillRecord]:
        return (
            await self._report_for_run(run_id),
            await self._fill_record_for_run(run_id),
        )


__all__ = [
    "ANSWER_PROMPT",
    "CRITIC_PROMPT",
    "EvaluationAgent",
    "EvaluationContextReader",
    "render_fill_record",
]
