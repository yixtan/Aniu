"""龙虎榜 standardizers: one stock's listings and seats, and a day's list.

Read against 600487 亨通光电 on 2026-08-25 and the whole list for 2026-09-28:

- A listing is one stock, one date and one exchange reason. 002614 was
  listed twice on 09-28, for 换手率 and for 振幅, with the same seats and the
  same net −5,351.7 万 under both; 002342 was listed for a one-day reason
  (+7,634.9 万) and for 「连续三个交易日…累计」(+3,655.2 万), whose seats and
  amounts cover all three days. Summing a stock's rows double-counts, so
  seats stay grouped by ``TRADE_ID``, and a date's totals count a seat row
  that recurs across reasons once.
- A seat on both top-five lists appears once in each table, and each row
  carries only its own side: 沪股通专用 bought 10.89 亿 and sold 3.35 亿 of
  600487 that day, but the buy row's ``SELL`` is null. The other side is
  taken from the other table by seat code, and is null — not disclosed, not
  zero — when the seat did not make that list.
- Every 机构专用 seat has code ``0``, so they cannot be matched across sides:
  an institution's other side is always null, and institutions are counted
  one per seat row.
- The day's list is ordered by the size of the net, buying or selling. Most
  days have more listings than the default ``limit``, and a list ordered by net
  buying would cut exactly the heaviest net sellers — 机构 dumping a stock
  that went 跌停 — which weigh as much for sentiment as the buyers.
- 沪股通专用 and 深股通专用 are one seat each (沪股通 is code ``10434470``), so
  they merge across sides like a brokerage branch does.

Amounts are 元 as published; ``CHANGE_RATE`` and ``TURNOVERRATE`` are already
percent (6.9005 is +6.90%).
"""

from __future__ import annotations

from collections.abc import Callable

from backend.stock_api.public.contracts import (
    DragonTigerMarketRequest,
    DragonTigerStockRequest,
    symbol_code,
)
from backend.stock_api.public.errors import UpstreamUnavailable
from backend.stock_api.public.normalizers.common import (
    JsonRecord,
    NormalizedData,
    as_record,
    canonical_symbol,
    first_number,
    first_text,
    normalize_date,
    number,
    text,
)

STOCK_BASIS = (
    "金额为龙虎榜披露席位的买卖额（元），不是全部成交；机构专用席位共用代码 0，"
    "买卖两侧无法配对，数机构家数时每个 kind=institution 的席位行算一家；"
    "席位某一侧为 null 表示那一侧没进前五、没有披露，不是 0；"
    "「连续三个交易日」类原因的金额是三日累计。"
)
MARKET_BASIS = (
    "金额为龙虎榜披露席位的买卖额（元），不是全部成交；一只股票可因不同原因"
    "多次上榜，各行统计窗口不同（「连续三个交易日」类为三日累计），不能相加；"
    "按净额的绝对值从大到小排列，净买入和净卖出最大的都在前面，被 limit "
    "截掉的是净额最小的。"
)
MARKET_EMPTY_NOTE = "该日龙虎榜为空或尚未公布（通常收盘后约 17:00–18:00 公布）。"

_CONNECT_SEATS = ("沪股通专用", "深股通专用")


def normalize_dragon_tiger_stock(
    raw: object, request: DragonTigerStockRequest
) -> NormalizedData:
    root = as_record(raw)
    records = root.get("records") if root is not None else None
    if root is None or not isinstance(records, list):
        raise UpstreamUnavailable("东方财富龙虎榜响应无效。")
    code = symbol_code(request.symbol)
    rows = [row for row in records if isinstance(row, dict)]
    items = [item for row in rows if (item := _record_item(row, code))]
    if rows and not items:
        raise UpstreamUnavailable("东方财富龙虎榜响应中的关键字段无效。")
    items.sort(key=lambda item: str(item["date"]), reverse=True)
    items = items[: request.limit]
    since = normalize_date(root.get("since"))
    # The basis explains amounts and seats; with no listing there are none.
    warnings = [STOCK_BASIS] if items else []
    degraded = False
    seats: dict[str, object] | None = None
    note: str | None = None
    seat_date = normalize_date(root.get("seat_date"))
    seat_error = text(root.get("seat_error"))
    if not items:
        note = f"{since} 以来没有上过龙虎榜。" if since else "近期没有上过龙虎榜。"
    elif seat_error:
        degraded = True
        warnings.append(f"席位明细这次没取到（{seat_error}），只返回了上榜记录。")
    elif seat_date is not None:
        seats, multi_window = _seats(root, items, code, seat_date)
        if seats is None:
            listed = any(item["date"] == seat_date for item in items)
            note = (
                f"{seat_date} 的席位明细还没有公布或为空。"
                if listed
                else f"{seat_date} 这只股票没有上龙虎榜；最近的上榜见 items。"
            )
        elif multi_window:
            warnings.append(
                f"{seat_date} 有几条上榜原因且席位不同（统计窗口不同），"
                "seats 的合计把它们加在一起了，按每条 listing 自己的净额看更准。"
            )
    return NormalizedData(
        {
            "symbol": request.symbol,
            "since": since,
            "items": items,
            "seats": seats,
            "note": note,
        },
        degraded=degraded,
        warnings=tuple(warnings),
        # Only a failed seat request degrades this answer, and asking again
        # may bring the seats.
        partial=degraded,
    )


def normalize_dragon_tiger_market(
    raw: object, request: DragonTigerMarketRequest
) -> NormalizedData:
    """Raw is `dragon_tiger_market`'s ``{"rows", "expected_count"}``."""

    root = as_record(raw)
    raw_rows = root.get("rows") if root is not None else None
    if root is None or not isinstance(raw_rows, list):
        raise UpstreamUnavailable("东方财富全市场龙虎榜响应无效。")
    rows = [row for row in raw_rows if isinstance(row, dict)]
    items = [item for row in rows if (item := _market_item(row, request.trade_date))]
    if rows and not items:
        raise UpstreamUnavailable("东方财富全市场龙虎榜响应中的关键字段无效。")
    # Counted before the cut: a stock listed for three reasons is three rows.
    row_count = len(items)
    stock_count = len({item["code"] for item in items})
    items.sort(key=lambda item: _by_size(item["net_amount"]))
    warnings = [MARKET_BASIS] if items else []
    expected = root.get("expected_count")
    short = (
        isinstance(expected, int)
        and not isinstance(expected, bool)
        and len(rows) < expected
    )
    if short:
        # Paged by code, so what is missing is the end of the code order.
        warnings.append(
            f"东方财富只返回了当天 {expected} 行中的 {len(rows)} 行，"
            "row_count 和 stock_count 偏低；缺的是代码靠后的股票"
            "（沪市、科创板、北交所）。"
        )
    return NormalizedData(
        {
            "trade_date": request.trade_date,
            "row_count": row_count,
            "stock_count": stock_count,
            "items": items[: request.limit],
            "note": None if items else MARKET_EMPTY_NOTE,
        },
        degraded=short,
        warnings=tuple(warnings),
    )


def _record_item(row: JsonRecord, code: str) -> dict[str, object] | None:
    day = normalize_date(row.get("TRADE_DATE"))
    if day is None or first_text(row, "SECURITY_CODE") not in {"", code}:
        return None
    return {
        "date": day,
        "reason": first_text(row, "EXPLANATION") or None,
        "trade_id": first_text(row, "TRADE_ID") or None,
        "close": first_number(row, "CLOSE_PRICE"),
        "change_percent": _percent(row, "CHANGE_RATE"),
        "turnover_rate": _percent(row, "TURNOVERRATE"),
        **_amounts(row),
        "commentary": first_text(row, "EXPLAIN") or None,
    }


def _market_item(row: JsonRecord, trade_date: str) -> dict[str, object] | None:
    code = first_text(row, "SECURITY_CODE")
    if len(code) != 6 or normalize_date(row.get("TRADE_DATE")) != trade_date:
        return None
    return {
        "code": code,
        # 北交所 920xxx and the like are listed too; canonical_symbol has no
        # form for them, so they keep their code and name and no symbol.
        "symbol": canonical_symbol(first_text(row, "SECUCODE") or code),
        "name": first_text(row, "SECURITY_NAME_ABBR") or None,
        "reason": first_text(row, "EXPLANATION") or None,
        "close": first_number(row, "CLOSE_PRICE"),
        "change_percent": _percent(row, "CHANGE_RATE"),
        "turnover_rate": _percent(row, "TURNOVERRATE"),
        **_amounts(row),
        "board": first_text(row, "TRADE_MARKET") or None,
    }


def _amounts(row: JsonRecord) -> dict[str, float | None]:
    buy = _money(first_number(row, "BILLBOARD_BUY_AMT", "SUM_BUY_AMT"))
    sell = _money(first_number(row, "BILLBOARD_SELL_AMT", "SUM_SELL_AMT"))
    net = _money(first_number(row, "BILLBOARD_NET_AMT", "NET_BS_AMT"))
    if net is None and buy is not None and sell is not None:
        net = round(buy - sell, 2)
    return {"buy_amount": buy, "sell_amount": sell, "net_amount": net}


# Seats ----------------------------------------------------------------------


def _seats(
    root: JsonRecord,
    items: list[dict[str, object]],
    code: str,
    seat_date: str,
) -> tuple[dict[str, object] | None, bool]:
    """The seats of every listing on ``seat_date``, and whether those
    listings disagree (different windows, so their totals do not add)."""

    sides = {
        side: [
            row
            for row in _side_rows(root.get(side))
            if normalize_date(row.get("TRADE_DATE")) == seat_date
            and first_text(row, "SECURITY_CODE") in {"", code}
        ]
        for side in ("buy", "sell")
    }
    if not sides["buy"] and not sides["sell"]:
        return None, False
    reasons = {
        text(item["trade_id"]): text(item["reason"])
        for item in items
        if item["date"] == seat_date and item["trade_id"]
    }
    # Listings in the order the records put them, then any only the seats
    # name, such as a listing older than the page of records.
    order = list(reasons)
    for row in (*sides["buy"], *sides["sell"]):
        trade_id = first_text(row, "TRADE_ID")
        if trade_id not in order:
            order.append(trade_id)
    listings = []
    seat_sets: set[tuple[frozenset[tuple[str, str]], ...]] = set()
    for trade_id in order:
        listing_sides = {
            side: [row for row in rows if first_text(row, "TRADE_ID") == trade_id]
            for side, rows in sides.items()
        }
        rows = (*listing_sides["buy"], *listing_sides["sell"])
        reason = reasons.get(trade_id) or (
            first_text(rows[0], "EXPLANATION") if rows else ""
        )
        listings.append(_listing(trade_id, reason or None, listing_sides))
        if not rows:
            continue
        seat_sets.add(
            (
                frozenset(_row_key(row, "BUY") for row in listing_sides["buy"]),
                frozenset(_row_key(row, "SELL") for row in listing_sides["sell"]),
            )
        )
    return {
        "date": seat_date,
        "listings": listings,
        "institution_net": _distinct_net(sides, _is_institution),
        "connect_net": _distinct_net(sides, _is_connect),
    }, len(seat_sets) > 1


def _listing(
    trade_id: str,
    reason: str | None,
    sides: dict[str, list[JsonRecord]],
) -> dict[str, object]:
    bought = _amounts_by_seat(sides["buy"], "BUY")
    sold = _amounts_by_seat(sides["sell"], "SELL")
    buy = [
        _seat(row, _amount(row, "BUY"), _other_side(row, sold)) for row in sides["buy"]
    ]
    sell = [
        _seat(row, _other_side(row, bought), _amount(row, "SELL"))
        for row in sides["sell"]
    ]
    buy.sort(key=lambda seat: _descending(seat["buy_amount"]))
    sell.sort(key=lambda seat: _descending(seat["sell_amount"]))
    return {
        "trade_id": trade_id or None,
        "reason": reason,
        "buy": buy,
        "sell": sell,
        "institution_net": _net(sides, _is_institution),
        "connect_net": _net(sides, _is_connect),
    }


def _seat(
    row: JsonRecord, buy_amount: float | None, sell_amount: float | None
) -> dict[str, object]:
    kind = (
        "institution"
        if _is_institution(row)
        else "connect"
        if _is_connect(row)
        else "other"
    )
    return {
        "name": first_text(row, "OPERATEDEPT_NAME") or None,
        "buy_amount": buy_amount,
        "sell_amount": sell_amount,
        "kind": kind,
    }


def _merge_key(row: JsonRecord) -> str | None:
    """The seat's identity across the two tables; None for 机构专用, whose
    shared code ``0`` says nothing about which institution it was."""

    if _is_institution(row):
        return None
    return first_text(row, "OPERATEDEPT_CODE", "OPERATEDEPT_NAME") or None


def _amounts_by_seat(rows: list[JsonRecord], column: str) -> dict[str, float | None]:
    return {key: _amount(row, column) for row in rows if (key := _merge_key(row))}


def _other_side(row: JsonRecord, amounts: dict[str, float | None]) -> float | None:
    key = _merge_key(row)
    return None if key is None else amounts.get(key)


def _is_institution(row: JsonRecord) -> bool:
    return first_text(row, "OPERATEDEPT_CODE") == "0" or "机构专用" in first_text(
        row, "OPERATEDEPT_NAME"
    )


def _is_connect(row: JsonRecord) -> bool:
    name = first_text(row, "OPERATEDEPT_NAME")
    return any(seat in name for seat in _CONNECT_SEATS)


def _net(
    sides: dict[str, list[JsonRecord]], kind: Callable[[JsonRecord], bool]
) -> float | None:
    """Buys from the buy table minus sells from the sell table, each row once;
    None when no seat of that kind made either list."""

    buys = [_amount(row, "BUY") or 0.0 for row in sides["buy"] if kind(row)]
    sells = [_amount(row, "SELL") or 0.0 for row in sides["sell"] if kind(row)]
    if not buys and not sells:
        return None
    return round(sum(buys) - sum(sells), 2)


def _distinct_net(
    sides: dict[str, list[JsonRecord]], kind: Callable[[JsonRecord], bool]
) -> float | None:
    """`_net` over the whole date, with a seat row that two reasons share —
    same seat, same side, same amount — counted once."""

    return _net(
        {
            side: list({_row_key(row, column): row for row in sides[side]}.values())
            for side, column in (("buy", "BUY"), ("sell", "SELL"))
        },
        kind,
    )


def _row_key(row: JsonRecord, column: str) -> tuple[str, str]:
    return (
        first_text(row, "OPERATEDEPT_CODE", "OPERATEDEPT_NAME"),
        text(row.get(column)),
    )


def _side_rows(value: object) -> list[JsonRecord]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise UpstreamUnavailable("东方财富龙虎榜席位响应不是列表。")
    return [row for row in value if isinstance(row, dict)]


# Numbers --------------------------------------------------------------------


def _descending(value: object) -> tuple[bool, float]:
    """Sort key: largest first, missing values last."""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return False, -float(value)
    return True, 0.0


def _by_size(value: object) -> tuple[bool, float]:
    """Sort key: largest magnitude first, either sign; missing values last."""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return False, -abs(float(value))
    return True, 0.0


def _amount(row: JsonRecord, key: str) -> float | None:
    return _money(number(row.get(key)))


def _money(value: float | int | None) -> float | None:
    return None if value is None else round(float(value), 2)


def _percent(row: JsonRecord, key: str) -> float | None:
    value = first_number(row, key)
    return None if value is None else round(float(value), 4)


__all__ = [
    "MARKET_BASIS",
    "MARKET_EMPTY_NOTE",
    "STOCK_BASIS",
    "normalize_dragon_tiger_market",
    "normalize_dragon_tiger_stock",
]
