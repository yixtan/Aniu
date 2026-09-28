"""Sina money-flow standardizers.

Sina and East Money split orders into size classes at different thresholds,
so one stock's 主力净流入 differs between them, sometimes in sign: for 600487 on
2026-09-28 East Money said −19.1 亿 and Sina −12.1 亿. Every result from here
says which basis it is on, because the agent keeps numbers in long-term memory
and would otherwise compare one source's figure with the other's.

Sina's fields, as its moneyflow pages use them:

- ``r0_*`` is Sina's 主力, its largest order class; ``netamount`` is the net
  across every class, which is not 主力.
- ``changeratio``, ``avg_changeratio`` and ``r0_ratio`` are fractions:
  0.0999 means 9.99%.
- ``turnover`` is the turnover rate in hundredths of a percent: 469.9 means
  4.699%.

The history parser read ``netamount`` as 主力 and ``changeratio`` as a percent
before this module existed, so 600487's limit-down day showed as −0.1%.
"""

from __future__ import annotations

from backend.stock_api.public.contracts import (
    StockMoneyFlowHistoryRequest,
)
from backend.stock_api.public.errors import UpstreamUnavailable
from backend.stock_api.public.normalizers.common import (
    JsonRecord,
    NormalizedData,
    first_number,
    first_text,
    require_items,
)

HISTORY_BASIS = (
    "新浪口径：main_net_inflow 是新浪的主力（它最大一档单子）净额，"
    "net_inflow 是全部单子的净额；分档门槛和东方财富不同。"
)


def normalize_sina_money_history(
    raw: object, request: StockMoneyFlowHistoryRequest
) -> NormalizedData:
    rows = _rows(raw)
    items = [item for row in rows if (item := _history_item(row))]
    # Sina pages newest first; oldest first matches East Money and reads as a
    # series.
    items = items[: request.limit][::-1]
    require_items(items, "资金流")
    return NormalizedData(
        {
            "symbol": request.symbol,
            "items": items,
            "page": request.page,
            "limit": request.limit,
        },
        degraded=True,
        warnings=(HISTORY_BASIS,),
    )


def _rows(raw: object) -> list[JsonRecord]:
    if raw is None or raw == "":
        return []
    if not isinstance(raw, list):
        raise UpstreamUnavailable("新浪资金流响应不是列表。")
    return [row for row in raw if isinstance(row, dict)]


def _history_item(row: JsonRecord) -> dict[str, object] | None:
    time = first_text(row, "opendate")
    if not time:
        return None
    return {
        "time": time,
        "main_net_inflow": first_number(row, "r0_net"),
        "main_net_ratio": _percent(row, "r0_ratio"),
        "net_inflow": first_number(row, "netamount"),
        "close": first_number(row, "trade"),
        "change_percent": _percent(row, "changeratio"),
        "turnover_rate": _turnover_rate(row),
    }


def _percent(row: JsonRecord, key: str) -> float | None:
    value = first_number(row, key)
    return None if value is None else round(value * 100, 4)


def _turnover_rate(row: JsonRecord) -> float | None:
    value = first_number(row, "turnover")
    return None if value is None else round(value / 100, 4)


__all__ = ["normalize_sina_money_history"]
