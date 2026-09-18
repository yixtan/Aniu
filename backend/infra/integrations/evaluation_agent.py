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

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from backend.agent.harness import AgentHarness
from backend.agent.kernel.runtime_config import LlmRuntimeConfig
from backend.business.evaluations import (
    MAX_CANDIDATE_LENGTH,
    MAX_CANDIDATES,
    MAX_DIGEST_LENGTH,
    EvaluationResult,
    EvaluatorPort,
    FindingCandidate,
)
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
4. 每个问题配一句「为什么问」，**先用一句不带行话的话说清这是什么问题**，
   再给出依据的具体数字。读的人可能不熟悉盘口术语，这一句要让他看得懂。
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

DIGEST_PROMPT = """你读到一场独立评估：一位第三方评估者的提问，
以及这次运行的执行者的回答。

你要产出两样东西：一段导读，和 2 到 3 条**未结议题**候选。

## 一、导读（150 字以内）

写给一个**不熟悉盘口术语的人**看，让他在不读下面几千字的情况下知道：
这次评估主要在担心什么、哪一条最要紧、有没有需要他拿主意的地方。

- 说人话。敞口、口径、净流、超大单、VWAP、深档浅档这类词，要么不用，
  要么用括号顺带解释一次。
- 只指路，不下结论。你是在告诉他「下面哪一段值得看」，
  不是替他判断谁对谁错——结论要他自己看原文得出。
- 不要复述数字清单，不要写「本次评估共提出 5 个问题」这种废话开头。

## 二、候选

未结议题不是问题，是**主张**。立项之后，此后每一次操盘都必须对它逐条表态（已按此调整／不同意／待定），所以它必须是一句能被表态的断言，不是一个问号。

挑选标准，按优先级：
1. 执行者在回答里**承认了、或答不上来**的那些——那是问答暴露出来的，不是提问时就知道的。
2. **结构性**的问题：会在此后每一天重复出现的做法，而不是只属于这一天的一笔委托。
3. 提问者没问到、但对照问答能看出来的。这一条最有价值。

不要提：回答已经用证据说清楚的；只在这一天成立的；程序运维和接口调用类的。

每条候选包含两段，**各不超过 60 字**：
- finding：一句话的主张，说清楚「什么做法」会导致「什么后果」。
- resolution_test：什么证据会让这条议题了结。必须具体到能判断真假，
  但**只说判据，不开药方**——怎么改是运行自己的事，你写实施方案就等于
  替它做了决定，它接下来只会照抄而不会自己想。写不出判据的候选不要提。

还有两条禁令：
- 提问者的前提不等于事实。执行者已经用具体数字回应过的，不要再当成事实写进主张。
- 你手上没有的数，不要推算出来当依据。

## 输出

只输出一个 JSON 对象，不要任何其他文字：
{"digest": "……", "candidates": [{"finding": "……", "resolution_test": "……"}]}"""

OPERATOR_QUESTION_BRIEF = """## 账户的操作者另外有一个疑问

他不是这套系统的一部分，也不一定懂盘口术语，但账是他的。他写的是：

{question}

把它作为你的**第一个问题**，并且用你的方式把它问准——对照记录补上具体数字、
指出它真正该问的是什么。他的措辞可能不精确，你要问的是他关心的那件事。

这一条是**追加**，不是替换：你自己的 2 到 4 个问题照提。"""

MISSING_REPORT = "（这次运行没有留下 Markdown 报告。）"

logger = logging.getLogger(__name__)

_JSON_OBJECT = re.compile(r"{.*}", re.DOTALL)
_JSON_ARRAY = re.compile(r"\[.*]", re.DOTALL)

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


def parse_digest(text: str) -> tuple[str, tuple[FindingCandidate, ...]]:
    """Read the lead and the drafts out of whatever the model actually sent.

    Tolerant on purpose, and tolerant of each half separately. Both sit on top
    of a review that is already worth reading, so a fence around the object, a
    sentence before it, a missing lead or one malformed draft among three must
    cost at most that piece — never the questions and answers underneath.
    """

    match = _JSON_OBJECT.search(text)
    if match is not None:
        try:
            decoded: Any = json.loads(match.group(0))
        except json.JSONDecodeError:
            decoded = None
        # Carrying neither key means this is not the payload: the regex also
        # matches the first object inside a bare array, and reading that as
        # the whole answer would throw the drafts away.
        carries_payload = isinstance(decoded, dict) and (
            "digest" in decoded or "candidates" in decoded
        )
        if carries_payload:
            decoded = cast(dict[str, Any], decoded)
            return (
                _digest_text(decoded.get("digest")),
                _read_candidates(decoded.get("candidates")),
            )
    # A bare array is what the earlier prompt asked for, and what a model will
    # still send when it ignores the object wrapper. The drafts are the half
    # worth salvaging.
    array = _JSON_ARRAY.search(text)
    if array is None:
        return "", ()
    try:
        fallback: Any = json.loads(array.group(0))
    except json.JSONDecodeError:
        return "", ()
    return "", _read_candidates(fallback)


def _digest_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:MAX_DIGEST_LENGTH]


def _read_candidates(decoded: object) -> tuple[FindingCandidate, ...]:
    if not isinstance(decoded, list):
        return ()

    candidates: list[FindingCandidate] = []
    seen: set[str] = set()
    for entry in decoded:
        if not isinstance(entry, dict):
            continue
        finding = _candidate_text(entry.get("finding"))
        resolution_test = _candidate_text(entry.get("resolution_test"))
        if finding in seen:
            continue
        try:
            candidates.append(
                FindingCandidate(finding=finding, resolution_test=resolution_test)
            )
        except ValueError:
            # A draft missing its settling test is the one the person would
            # have to finish anyway; dropping it is what the gate is for.
            continue
        seen.add(finding)
        if len(candidates) == MAX_CANDIDATES:
            break
    return tuple(candidates)


def _candidate_text(value: object) -> str:
    # Truncated to what the raise endpoint accepts, so every draft on the page
    # is one the person can submit without editing it first.
    return value.strip()[:MAX_CANDIDATE_LENGTH] if isinstance(value, str) else ""


@dataclass(frozen=True, slots=True)
class _Digest:
    digest: str = ""
    candidates: tuple[FindingCandidate, ...] = ()
    total_tokens: int = 0
    cached_tokens: int = 0


@dataclass(slots=True)
class EvaluationAgent(EvaluatorPort):
    llm_client: LLMClientPort
    runtime_factory: AgentRuntimeFactory
    settings_repo: SettingsRepositoryPort
    context_reader: EvaluationContextReader

    async def evaluate(
        self, run_id: int, *, operator_question: str = ""
    ) -> EvaluationResult:
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
        # Omitted entirely when there is none, the way the watchlist is: asking
        # a reviewer to consider an empty question only buys a sentence saying
        # the operator had none.
        brief = operator_question.strip()
        asked = await critic.prompt(
            f"## 这次运行的报告\n\n{body}\n\n{rendered}"
            + (
                f"\n\n{OPERATOR_QUESTION_BRIEF.format(question=brief)}"
                if brief
                else ""
            )
        )
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
        answers = answered.content.strip()

        drafted = await self._write_digest(runtime, questions, answers)
        return EvaluationResult(
            questions=questions,
            answers=answers,
            digest=drafted.digest,
            candidates=drafted.candidates,
            total_tokens=(
                asked.usage.total_tokens
                + answered.usage.total_tokens
                + drafted.total_tokens
            ),
            cached_tokens=(
                asked.usage.cache_read
                + answered.usage.cache_read
                + drafted.cached_tokens
            ),
        )

    async def _write_digest(
        self, runtime: LlmRuntimeConfig, questions: str, answers: str
    ) -> _Digest:
        """Never fails the review it rides on.

        The questions and answers are the thing that was asked for; the lead
        and the drafts sit on top of them. A provider error here would
        otherwise throw away a review that already cost two calls and is
        already complete.
        """

        if not questions or not answers:
            # Nothing to draft from. The review already failed to produce the
            # thing drafts are made of, and a third call would only bill for
            # asking the model to invent objections out of an empty page.
            return _Digest()

        drafter = AgentHarness(
            runtime=runtime,
            llm_client=self.llm_client,
            system_prompt=DIGEST_PROMPT,
            label="Draft",
        )
        try:
            written = await drafter.prompt(
                f"## 评估者的提问\n\n{questions}\n\n## 执行者的回答\n\n{answers}"
            )
        except Exception:  # noqa: BLE001 - the review survives a failed digest
            logger.warning("writing the evaluation digest failed", exc_info=True)
            return _Digest()
        digest, candidates = parse_digest(written.content)
        return _Digest(
            digest=digest,
            candidates=candidates,
            total_tokens=written.usage.total_tokens,
            cached_tokens=written.usage.cache_read,
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
    "DIGEST_PROMPT",
    "OPERATOR_QUESTION_BRIEF",
    "EvaluationAgent",
    "EvaluationContextReader",
    "parse_digest",
    "render_fill_record",
]
