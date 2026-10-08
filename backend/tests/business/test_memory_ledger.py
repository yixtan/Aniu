"""The ledger a Run report ends with lists the memory writes that happened."""

from __future__ import annotations

from backend.business.runs.memory_ledger import memory_ledger, with_memory_ledger


def _write(
    operation: str,
    *,
    memory_id: int | None = None,
    item: dict[str, object] | None = None,
    status: str = "ok",
    error: str | None = None,
) -> dict[str, object]:
    """One memory_write call, shaped like ToolLoopResult.as_payload()."""

    arguments: dict[str, object] = {"operation": operation}
    if memory_id is not None:
        arguments["memory_id"] = memory_id
    record: dict[str, object] = {
        "tool_call_id": f"call-{operation}-{memory_id}",
        "tool_name": "memory_write",
        "arguments": arguments,
        "status": status,
        "content": None if item is None else {"status": "ok", "item": item},
        "is_error": status != "ok",
    }
    if error is not None:
        record["error"] = error
    return record


def _lines(ledger: str) -> list[str]:
    return ledger.splitlines()[2:]


def test_a_run_that_wrote_nothing_says_so() -> None:
    activity = (
        {"tool_name": "query_quote", "status": "ok", "content": {"price": 10}},
        {"tool_name": "memory_read", "status": "ok", "content": {"items": []}},
    )

    ledger = memory_ledger(activity)

    assert ledger.splitlines()[:2] == [
        "---",
        "**本次记忆写入**（系统按实际工具调用生成）",
    ]
    assert _lines(ledger) == ["- 本次没有写入记忆。"]


def test_a_created_memory_is_named_by_its_title() -> None:
    created = _write(
        "create",
        item={
            "id": 185,
            "version": 1,
            "content": (
                "【同一交易日内重复运行的处置口径（10/8立）】"
                "①当日已声明的上限不为重跑而重设"
            ),
            "replaces": [],
        },
    )

    assert _lines(memory_ledger([created])) == [
        "- 新建 id185：【同一交易日内重复运行的处置口径（10/8立）】"
    ]


def test_an_update_carries_its_new_version_and_what_it_absorbed() -> None:
    updated = _write(
        "update",
        memory_id=31,
        item={
            "id": 31,
            "version": 3,
            "content": "【普跌日逆势大涨不追高】③即使资金居首也不追",
            "replaces": [96],
        },
    )

    assert _lines(memory_ledger([updated])) == [
        "- 更新 id31 → 第 3 版：【普跌日逆势大涨不追高】（并入 id96）"
    ]


def test_a_failed_write_is_listed_as_failed_not_dropped() -> None:
    rejected = _write(
        "update",
        memory_id=186,
        status="error",
        error="memory version conflict: expected 1, found 2",
    )

    assert _lines(memory_ledger([rejected])) == [
        "- 写入失败：更新 id186（memory version conflict: expected 1, found 2）"
    ]


def test_a_title_without_brackets_is_clipped() -> None:
    long_first_line = "高位轮动转普跌的仓位纪律" + "，" * 80
    updated = _write(
        "update",
        memory_id=30,
        item={"id": 30, "version": 2, "content": long_first_line, "replaces": []},
    )

    (line,) = _lines(memory_ledger([updated]))

    assert line.startswith("- 更新 id30 → 第 2 版：高位轮动转普跌的仓位纪律")
    assert line.endswith("……")
    assert len(line) < len(long_first_line)


def test_every_write_is_listed_in_order() -> None:
    activity = [
        _write(
            "create",
            item={"id": 186, "version": 1, "content": "【A】", "replaces": []},
        ),
        {"tool_name": "trade", "status": "ok", "content": {"orderId": "1"}},
        _write(
            "update",
            memory_id=188,
            item={"id": 188, "version": 2, "content": "【B】", "replaces": []},
        ),
    ]

    assert _lines(memory_ledger(activity)) == [
        "- 新建 id186：【A】",
        "- 更新 id188 → 第 2 版：【B】",
    ]


def test_the_ledger_follows_the_report_after_a_blank_line() -> None:
    # Directly under a line of text, "---" would make that line a heading.
    report = with_memory_ledger("# Report\n\nNo trade.", [])

    assert report.startswith("# Report\n\nNo trade.\n\n---\n")
