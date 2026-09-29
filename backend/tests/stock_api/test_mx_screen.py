"""select_stocks hands the model the screen's answer, not the page it came in.

From 2026-09-25 to 09-29 one call ran from 15,000 to 214,000 characters, most
of it ``allResults``: every matching stock with every field, and twenty-odd
display settings per column.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from backend.infra.integrations.mx_agent_tools import SelectStocksTool
from backend.infra.integrations.mx_screen import compact_screen_result

_FIXTURE = Path(__file__).parent / "fixtures" / "mx_stock_screen.json"


def _screen() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _with_every_stock(envelope: dict[str, Any]) -> dict[str, Any]:
    """Fill ``allResults`` the way the real one is filled: 111 full rows."""

    grown = copy.deepcopy(envelope)
    result = grown["data"]["data"]["allResults"]["result"]
    result["dataList"] = [
        {
            "SECURITY_CODE": f"{index:06d}",
            "SECURITY_SHORT_NAME": "样本",
            "概念": "概念" * 200,
        }
        for index in range(111)
    ]
    return grown


def test_the_answer_is_the_conditions_the_count_and_the_first_ten() -> None:
    envelope = _with_every_stock(_screen())
    body = envelope["data"]["data"]

    result = compact_screen_result(envelope)

    assert isinstance(result, dict)
    assert result["query"] == "光模块 CPO 概念成分股"
    assert result["total_condition"] == body["totalCondition"]
    assert result["conditions"] == body["responseConditionList"]
    assert result["matched"] == 111
    assert result["table"] == body["partialResults"].strip()
    assert "|1|301251|威尔高|" in result["table"]
    assert "|10|920060|万源通|" in result["table"]
    assert result["note"] == (
        "共 111 只符合条件，表中是按查询顺序排在最前的 10 只；"
        "要看别的，把条件收窄或换个排序再筛。"
    )
    rendered = json.dumps(result, ensure_ascii=False)
    assert "allResults" not in rendered
    assert len(rendered) < len(json.dumps(envelope, ensure_ascii=False)) // 10


def test_a_screen_that_found_everything_it_shows_needs_no_note() -> None:
    envelope = _screen()
    envelope["data"]["data"]["securityCount"] = 10

    result = compact_screen_result(envelope)

    assert isinstance(result, dict)
    assert "note" not in result


def test_a_screen_that_found_nothing_says_so() -> None:
    envelope = _screen()
    envelope["data"]["data"]["securityCount"] = 0
    envelope["data"]["data"]["partialResults"] = ""

    result = compact_screen_result(envelope)

    assert isinstance(result, dict)
    assert result["matched"] == 0
    assert result["note"] == "没有符合条件的股票。"


def test_an_unknown_shape_is_passed_through_whole_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A long answer costs tokens; a missing one costs the run its candidates."""

    envelope = _screen()
    del envelope["data"]["data"]["partialResults"]

    with caplog.at_level(logging.WARNING):
        assert compact_screen_result(envelope) is envelope

    assert "mx_screen_result_unrecognised" in caplog.text


@pytest.mark.parametrize("envelope", [None, "text", {"success": False}, {"data": []}])
def test_a_response_without_a_screen_body_is_left_alone(envelope: object) -> None:
    assert compact_screen_result(envelope) is envelope


@pytest.mark.asyncio
async def test_the_tool_returns_the_compact_answer() -> None:
    class _Research:
        async def select_stocks(self, keyword: str) -> dict[str, Any]:
            assert keyword == "光模块 CPO 概念成分股"
            return _with_every_stock(_screen())

    tool = SelectStocksTool(client=_Research())  # type: ignore[arg-type]

    result = await tool.run("光模块 CPO 概念成分股")

    assert isinstance(result, dict)
    assert result["matched"] == 111
    assert "allResults" not in json.dumps(result, ensure_ascii=False)
