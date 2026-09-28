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
    SectorMoneyFlowRequest,
    SectorRankingRequest,
    StockMoneyFlowHistoryRequest,
    StockMoneyFlowIntradayRequest,
    StockRankingRequest,
)
from backend.stock_api.public.errors import NoStockData, UpstreamUnavailable
from backend.stock_api.public.normalizers.common import (
    JsonRecord,
    NormalizedData,
    as_record,
    canonical_symbol,
    first_number,
    first_text,
    require_items,
)

STOCK_BASIS = (
    "新浪口径：净流入是新浪的主力（它最大一档单子）净额，分档门槛和东方财富不同，"
    "同一只股票的数值不能和东方财富的直接比较。"
)
HISTORY_BASIS = (
    "新浪口径：main_net_inflow 是新浪的主力（它最大一档单子）净额，"
    "net_inflow 是全部单子的净额；分档门槛和东方财富不同。"
)
TODAY_BASIS = (
    "新浪只有当天累计值，没有分钟序列；main_net_inflow 是新浪的主力"
    "（它最大一档单子）净额，口径和东方财富不同。"
)
SECTOR_BASIS = (
    "新浪口径：板块是新浪自己的行业、概念划分，和东方财富的不一一对应；"
    "净流入是板块内全部单子的净额，不是主力净流入；涨跌幅是成分股的平均涨跌幅。"
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


def normalize_sina_money_today(
    raw: object, request: StockMoneyFlowIntradayRequest
) -> NormalizedData:
    row = as_record(raw)
    if not row:
        raise NoStockData("新浪没有这只股票当天的资金流。")
    main_in = first_number(row, "r0_in")
    main_out = first_number(row, "r0_out")
    if main_in is None or main_out is None:
        raise UpstreamUnavailable("新浪当天资金流响应缺少主力字段。")
    return NormalizedData(
        {
            "symbol": request.symbol,
            "items": [
                {
                    "time": "当日累计",
                    "main_net_inflow": main_in - main_out,
                    "net_inflow": first_number(row, "netamount"),
                    "close": first_number(row, "trade"),
                    "change_percent": _percent(row, "changeratio"),
                    "turnover_rate": _turnover_rate(row),
                }
            ],
            "sampled": False,
        },
        degraded=True,
        warnings=(TODAY_BASIS,),
    )


def normalize_sina_stock_money_ranking(
    raw: object, request: StockRankingRequest
) -> NormalizedData:
    """Sina ranks ETFs alongside stocks; they are dropped here, and the page
    is cut from the deeper first page the adapter fetched."""

    items = [item for row in _rows(raw) if (item := _stock_item(row))]
    start = (request.page - 1) * request.limit
    items = items[start : start + request.limit]
    require_items(items, "个股排行")
    _require_sort_values(items, "net_inflow")
    return NormalizedData(
        {"page": request.page, "limit": request.limit, "items": items},
        degraded=True,
        warnings=(STOCK_BASIS,),
    )


def normalize_sina_sector_money(
    raw: object, request: SectorRankingRequest | SectorMoneyFlowRequest
) -> NormalizedData:
    items = [item for row in _rows(raw) if (item := _sector_item(row))]
    items = items[: request.limit]
    require_items(items, "板块排行")
    sort = "net_inflow" if isinstance(request, SectorMoneyFlowRequest) else request.sort
    _require_sort_values(items, sort)
    data: dict[str, object] = {
        "page": request.page,
        "limit": request.limit,
        "items": items,
    }
    if isinstance(request, SectorMoneyFlowRequest):
        data = {"sector_type": request.sector_type, **data}
    return NormalizedData(data, degraded=True, warnings=(SECTOR_BASIS,))


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


def _stock_item(row: JsonRecord) -> dict[str, object] | None:
    # canonical_symbol accepts A-share stock codes only, so this is where
    # the ETFs in Sina's list fall out.
    symbol = canonical_symbol(first_text(row, "symbol"))
    if symbol is None:
        return None
    return {
        "symbol": symbol,
        "code": symbol[:6],
        "name": first_text(row, "name"),
        "price": first_number(row, "trade"),
        "change": None,
        "change_percent": _percent(row, "changeratio"),
        "volume_shares": None,
        "amount": first_number(row, "amount"),
        "turnover_rate": _turnover_rate(row),
        "net_inflow": first_number(row, "r0_net"),
    }


def _sector_item(row: JsonRecord) -> dict[str, object] | None:
    name = first_text(row, "name")
    if not name:
        return None
    leader = canonical_symbol(first_text(row, "ts_symbol"))
    return {
        "id": first_text(row, "category") or None,
        "name": name,
        # avg_price is the mean of member prices, not a board index level.
        "price": None,
        "change": None,
        "change_percent": _percent(row, "avg_changeratio"),
        "volume_shares": None,
        "amount": None,
        "turnover_rate": _turnover_rate(row),
        "net_inflow": first_number(row, "netamount"),
        "leader_symbol": leader,
        "leader_name": first_text(row, "ts_name") or None,
        "leader_change_percent": _percent(row, "ts_changeratio"),
    }


def _percent(row: JsonRecord, key: str) -> float | None:
    value = first_number(row, key)
    return None if value is None else round(value * 100, 4)


def _turnover_rate(row: JsonRecord) -> float | None:
    value = first_number(row, "turnover")
    return None if value is None else round(value / 100, 4)


def _require_sort_values(items: list[dict[str, object]], field: str) -> None:
    if not any(isinstance(item.get(field), (int, float)) for item in items):
        raise UpstreamUnavailable("新浪排行响应中的排序字段全部无效。")


__all__ = [
    "normalize_sina_money_history",
    "normalize_sina_money_today",
    "normalize_sina_sector_money",
    "normalize_sina_stock_money_ranking",
]
