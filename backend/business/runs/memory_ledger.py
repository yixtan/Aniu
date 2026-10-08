"""The memory writes a Run actually made, appended to its report.

The report is the model's own account of the run, and on 2026-10-08 one said
it had folded a rule into id186 without ever calling memory_write: it settled
on the update, wrote the report, and stopped. Nothing failed, so nothing showed;
the dream caught it only because it happened to compare that report with the
store. The ledger is the other side of that comparison, written from the tool
calls themselves, so whoever reads the report — the dream, the evaluator, the
person — can hold what the run says against what it did.

The model's own account stays. It is the only record of what a missing write
was meant to say, and that is what let the dream restore it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

MEMORY_WRITE_TOOL = "memory_write"
LEDGER_HEADING = "**本次记忆写入**（系统按实际工具调用生成）"
NO_WRITES_LINE = "- 本次没有写入记忆。"

_OPERATION_LABELS = {"create": "新建", "update": "更新", "delete": "删除"}
_TITLE_LIMIT = 60
_ERROR_LIMIT = 80


def with_memory_ledger(
    report: str, tool_activity: Iterable[Mapping[str, object]]
) -> str:
    return f"{report}\n\n{memory_ledger(tool_activity)}"


def memory_ledger(tool_activity: Iterable[Mapping[str, object]]) -> str:
    lines = [
        _ledger_line(item)
        for item in tool_activity
        if item.get("tool_name") == MEMORY_WRITE_TOOL
    ]
    # The blank line above keeps this a rule rather than turning the report's
    # last line into a heading.
    return "\n".join(("---", LEDGER_HEADING, *(lines or [NO_WRITES_LINE])))


def _ledger_line(item: Mapping[str, object]) -> str:
    arguments = _mapping(item.get("arguments"))
    operation = str(arguments.get("operation") or "")
    label = _OPERATION_LABELS.get(operation, operation or "写入")
    if item.get("status") != "ok":
        target = _memory_ref(arguments.get("memory_id"))
        error = _clip(str(item.get("error") or "未说明原因"), _ERROR_LIMIT)
        return f"- 写入失败：{label}{target}（{error}）"
    written = _mapping(_mapping(item.get("content")).get("item"))
    memory_ref = _memory_ref(written.get("id") or arguments.get("memory_id"))
    line = f"- {label}{memory_ref}"
    version = written.get("version")
    if operation == "update" and version is not None:
        line += f" → 第 {version} 版"
    title = _title(written.get("content"))
    if title:
        line += f"：{title}"
    replaces = written.get("replaces")
    if isinstance(replaces, list) and replaces:
        line += "（并入 " + "、".join(f"id{value}" for value in replaces) + "）"
    return line


def _title(content: object) -> str:
    if not isinstance(content, str) or not content.strip():
        return ""
    first_line = content.strip().splitlines()[0]
    # Memories open with a 【title】; that is the part that names the rule.
    if first_line.startswith("【") and "】" in first_line:
        first_line = first_line[: first_line.index("】") + 1]
    return _clip(first_line, _TITLE_LIMIT)


def _memory_ref(value: object) -> str:
    return "" if value in (None, "") else f" id{value}"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "……"


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


__all__ = ["memory_ledger", "with_memory_ledger"]
