"""What select_stocks hands the model: the screen's answer, not its page.

MX's stock-screen endpoint answers with the page its own UI renders. Beside
the answer, which is the conditions it understood, how many stocks matched and
a formatted table of the first ten, it carries ``allResults``: every matching
stock with every field, plus some twenty display settings for every column.
From 2026-09-25 to 09-29 one call ran from 15,000 to 214,000 characters, and
all of it went back to the model on every later turn of the run.

The first ten, in the order the query asked for, are what MX itself shows as
its preview. That is more than a run needs: it names two candidates and looks
closely at five at most. The count says how many there were, so a model that
wants others can narrow the query or sort it another way.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def compact_screen_result(envelope: object) -> object:
    """The conditions, the count and MX's own table of the first ten.

    A response in a shape this does not know is passed through whole: a long
    answer costs tokens, a missing one costs the run its candidates.
    """

    body = _screen_body(envelope)
    if body is None:
        return envelope
    table = body.get("partialResults")
    matched = body.get("securityCount")
    if not isinstance(table, str) or type(matched) is not int:
        logger.warning(
            "mx_screen_result_unrecognised",
            extra={"body_keys": sorted(str(key) for key in body)},
        )
        return envelope

    table = table.strip()
    shown = _row_count(table)
    listed = body.get("responseConditionList")
    result: dict[str, object] = {
        "query": body.get("title"),
        "conditions": [
            {"describe": item.get("describe"), "stockCount": item.get("stockCount")}
            for item in (listed if isinstance(listed, list) else ())
            if isinstance(item, dict)
        ],
        "total_condition": body.get("totalCondition"),
        "matched": matched,
        "table": table,
    }
    if matched == 0:
        result["note"] = "没有符合条件的股票。"
    elif matched > shown:
        result["note"] = (
            f"共 {matched} 只符合条件，表中是按查询顺序排在最前的 {shown} 只；"
            "要看别的，把条件收窄或换个排序再筛。"
        )
    return result


def _screen_body(envelope: object) -> dict[str, object] | None:
    """``data.data`` of a successful stock-screen response."""

    if not isinstance(envelope, dict):
        return None
    outer = envelope.get("data")
    if not isinstance(outer, dict):
        return None
    body = outer.get("data")
    return body if isinstance(body, dict) else None


def _row_count(table: str) -> int:
    lines = [line for line in table.splitlines() if line.strip().startswith("|")]
    # The header and the |---| rule under it are not stocks.
    return max(0, len(lines) - 2)


__all__ = ["compact_screen_result"]
