"""Build notification events from raw agent write-tool payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping

from backend.business.notifications.models import (
    NotificationEvent,
    NotificationEventKind,
    TradeDirection,
)
from backend.business.shared.trading import (
    TRADE_TOOL_NAME,
    is_successful_trade_payload,
    trade_order_id,
)

CANCEL_TOOL_NAME = "cancel"

# The write tools only accept unambiguous instructions, so these mirror the
# accepted grammar instead of guessing at free-form text. Parsing is best
# effort: an unrecognised instruction still notifies, just without details.
_LIMIT_TRADE = re.compile(
    r"(?P<direction>买入|买|buy|卖出|卖|sell)\s*"
    r"(?P<stock_code>\d{6})(?:\.(?:SH|SZ))?\s+"
    r"(?P<price>[0-9]+(?:\.[0-9]+)?)\s+(?P<quantity>\d+)",
    flags=re.IGNORECASE,
)
_CANCEL_ALL = re.compile(
    r"^(?:一键撤单|撤销所有|撤销全部|cancel\s+all)$",
    flags=re.IGNORECASE,
)
_CANCEL_ORDER = re.compile(
    r"^(?:撤单|撤销|cancel)\s+(?P<first>[A-Za-z0-9_-]+)\s+"
    r"(?P<second>\d{6})(?:\.(?:SH|SZ))?\s*$",
    flags=re.IGNORECASE,
)
_CANCEL_ORDER_REVERSED = re.compile(
    r"^(?:撤单|撤销|cancel)\s+(?P<stock_code>\d{6})(?:\.(?:SH|SZ))?\s+"
    r"(?P<order_id>[A-Za-z0-9_-]+)\s*$",
    flags=re.IGNORECASE,
)
_BUY_TOKENS = frozenset({"买入", "买", "buy"})


def _instruction_of(arguments: object) -> str:
    if not isinstance(arguments, Mapping):
        return ""
    return str(arguments.get("instruction") or "").strip()


def parse_trade_instruction_details(instruction: str) -> dict[str, object]:
    """Extract direction, code, price and quantity from a limit order."""

    match = _LIMIT_TRADE.search(instruction)
    if match is None:
        return {}
    direction = (
        TradeDirection.BUY
        if match.group("direction").casefold() in _BUY_TOKENS
        else TradeDirection.SELL
    )
    details: dict[str, object] = {
        "direction": direction,
        "stock_code": match.group("stock_code"),
    }
    try:
        details["price"] = float(match.group("price"))
        details["quantity"] = int(match.group("quantity"))
    except ValueError:
        return details
    return details


def parse_cancel_instruction_details(instruction: str) -> dict[str, object]:
    """Extract the order id and code from a single-order cancel instruction."""

    text = instruction.strip()
    if _CANCEL_ALL.fullmatch(text):
        return {}
    reversed_match = _CANCEL_ORDER_REVERSED.fullmatch(text)
    if reversed_match is not None:
        return {
            "order_id": reversed_match.group("order_id"),
            "stock_code": reversed_match.group("stock_code"),
        }
    match = _CANCEL_ORDER.fullmatch(text)
    if match is None:
        return {}
    return {"order_id": match.group("first"), "stock_code": match.group("second")}


def trade_event_from_tool_payload(
    *,
    run_id: int,
    stage_name: str,
    payload: Mapping[str, object],
) -> NotificationEvent | None:
    """Return an event for an accepted ``trade`` or ``cancel`` tool call.

    The upstream transport raises on business-error envelopes, so a completed
    call already means the simulator accepted the instruction.
    """

    if str(payload.get("status") or "") != "ok":
        return None
    tool_name = str(payload.get("tool_name") or "")
    instruction = _instruction_of(payload.get("arguments"))
    content = payload.get("content")
    common: dict[str, object] = {
        "run_id": run_id,
        "stage_name": stage_name,
        "tool_call_id": str(payload.get("tool_call_id") or ""),
        "instruction": instruction,
    }
    if tool_name == TRADE_TOOL_NAME:
        if not is_successful_trade_payload(content):
            return None
        return NotificationEvent(
            kind=NotificationEventKind.ORDER_PLACED,
            order_id=trade_order_id(content),
            **common,  # type: ignore[arg-type]
            **parse_trade_instruction_details(instruction),  # type: ignore[arg-type]
        )
    if tool_name == CANCEL_TOOL_NAME:
        return NotificationEvent(
            kind=NotificationEventKind.ORDER_CANCELLED,
            **common,  # type: ignore[arg-type]
            **parse_cancel_instruction_details(instruction),  # type: ignore[arg-type]
        )
    return None


__all__ = [
    "CANCEL_TOOL_NAME",
    "parse_cancel_instruction_details",
    "parse_trade_instruction_details",
    "trade_event_from_tool_payload",
]
