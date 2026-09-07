"""Success detection for MX paper-trading write calls.

Both the run report's trade counter and the trade notification pipeline must
agree on what "the order went through" means, so the predicate lives here
instead of being restated by each caller.
"""

from __future__ import annotations

from collections.abc import Mapping

TRADE_TOOL_NAME = "trade"


def _order_id_of(content: Mapping[str, object]) -> str | None:
    for source in (content, content.get("data")):
        if not isinstance(source, Mapping):
            continue
        raw = source.get("orderId")
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return None


def trade_order_id(content: object) -> str | None:
    """Return the upstream order id when the payload carries one."""

    if not isinstance(content, Mapping):
        return None
    return _order_id_of(content)


def is_successful_trade_payload(content: object) -> bool:
    """True when an MX trade response reports an accepted order."""

    if not isinstance(content, Mapping):
        return False
    return (
        bool(content.get("success"))
        or str(content.get("code") or "") == "200"
        or _order_id_of(content) is not None
    )


def is_successful_trade_call(
    *,
    tool_name: str,
    status: str,
    content: object,
) -> bool:
    """True for a completed ``trade`` tool call that placed a real order."""

    if status != "ok" or tool_name != TRADE_TOOL_NAME:
        return False
    return is_successful_trade_payload(content)


__all__ = [
    "TRADE_TOOL_NAME",
    "is_successful_trade_call",
    "is_successful_trade_payload",
    "trade_order_id",
]
