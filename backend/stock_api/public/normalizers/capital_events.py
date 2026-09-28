"""限售解禁 and 融资融券 standardizers.

The two reports sit on one gateway but count in different units, and the
field names invite the wrong reading.

``RPT_LIFT_STAGE`` counts shares in 万股 and money in 万元, with ratios as
fractions. Its ``FREE_SHARES`` is the circulating total *after* the batch
lifts, not the batch: for 600487's 2027-06-21 tranche it is 246,011.2317 万股
against a batch (``CURRENT_FREE_SHARES``) of 646.606 万股, and the first
minus the second is the row before it, which is how the probe of 2026-09-29
pinned the meaning down. SKILL.md maps ``shares`` to ``FREE_SHARES`` and
calls ``FREE_RATIO`` a share of total capital; it is a share of the
circulating shares, and ``TOTAL_RATIO`` is the share of total capital.

A batch dated ``as_of`` is upcoming, not past: its shares start trading that
day, and a run before the open or during the session is exactly the one that
needs to see supply arriving.

Its reference price ``NEW`` is not one price. On scheduled rows it is the
latest close — 60.64 on 2026-09-29, 600487's limit-down close of 09-28 — and
it moves every day; on past rows it is frozen near the lift date. So a past
batch's market value and a coming one's are on different prices, and the
result says so.

``RPTA_WEB_RZRQ_GGMX`` is already in 元 and 股, and ``RZYEZB`` is already a
percent. It is published T+1: at 01:46 on 2026-09-29 the newest row was
09-24, and the 09-28 row (a limit-down day for 600487) was not out yet. That
09-24 row matched the Shanghai exchange's own figures to the unit. Its
balance is read, never rebuilt from the flows: yesterday's 融资余额 plus
today's buying minus repayment missed the reported balance by 26.0M 元 on
09-22 and 27.8M on 09-21.
"""

from __future__ import annotations

from datetime import date, timedelta

from backend.stock_api.public.contracts import (
    LiftScheduleRequest,
    MarginDetailRequest,
    symbol_code,
)
from backend.stock_api.public.errors import UpstreamUnavailable
from backend.stock_api.public.normalizers.common import (
    JsonRecord,
    NormalizedData,
    first_number,
    first_text,
    normalize_date,
)

LIFT_WINDOW_DAYS = 90
LIFT_PAST_LIMIT = 10

LIFT_BASIS = (
    "解禁口径：shares 是本批解禁股数（股），不是解禁后的流通股总数；"
    "market_value = 本批股数 × 参考价，"
    "未来批次用最新收盘价、过去批次用解禁日前后的价格，"
    "两组不能直接比较；股权激励类的未来批次还要看考核条件，数量和日期可能变。"
)
MARGIN_BASIS = (
    "两融口径：financing_net_buy = 融资买入额 − 融资偿还额；"
    "short_balance = 融券余量 × 当日收盘价，股价涨跌本身就会让它变，"
    "融券是增是减要看 short_shares；"
    "financing_balance_ratio 是融资余额占流通市值的百分比。"
)
NO_LIFT_RECORDS = "东方财富没有这只股票的限售解禁记录。"
NO_MARGIN_RECORDS = "东方财富没有这只股票的融资融券记录，通常是因为它不是两融标的。"


def normalize_lift_schedule(
    raw: object, request: LiftScheduleRequest
) -> NormalizedData:
    rows = _own_rows(raw, request.symbol, "SECURITY_CODE", "限售解禁")
    as_of = date.fromisoformat(request.as_of)
    horizon = as_of + timedelta(days=LIFT_WINDOW_DAYS)
    batches = [batch for row in rows if (batch := _lift_batch(row))]
    dropped = _require_some(rows, batches, "限售解禁")

    past = sorted(
        (batch for batch in batches if _day(batch) < as_of),
        key=_day,
        reverse=True,
    )
    upcoming = sorted(
        (batch for batch in batches if as_of <= _day(batch) <= horizon),
        key=_day,
    )
    later = sorted((batch for batch in batches if _day(batch) > horizon), key=_day)

    data: dict[str, object] = {
        "symbol": request.symbol,
        "as_of": request.as_of,
        "upcoming": upcoming,
        "items": past[:LIFT_PAST_LIMIT],
    }
    note = _lift_note(bool(rows), as_of, horizon, upcoming, past, later)
    if note:
        data["note"] = note
    warnings = [LIFT_BASIS] if batches else []
    if dropped:
        warnings.append(f"限售解禁有 {dropped} 条记录因关键字段无效被丢弃。")
    return NormalizedData(data, degraded=dropped > 0, warnings=tuple(warnings))


def normalize_margin_detail(
    raw: object, request: MarginDetailRequest
) -> NormalizedData:
    rows = _own_rows(raw, request.symbol, "SCODE", "融资融券")
    items = [item for row in rows if (item := _margin_day(row))]
    dropped = _require_some(rows, items, "融资融券")
    # The report is asked for newest first; sorted here so a reordered page
    # cannot put an old balance where the model reads "latest".
    items = sorted(items, key=lambda item: str(item["date"]), reverse=True)
    items = items[: request.limit]

    if items:
        # The T+1 caveat lives here, dated, and nowhere else.
        note = (
            f"最新一行是 {items[0]['date']} 收盘后的余额；"
            "两融 T+1 公布，这一行之后的交易日还没有数据。"
        )
        warnings = [MARGIN_BASIS]
    else:
        note = NO_MARGIN_RECORDS
        warnings = []
    if dropped:
        warnings.append(f"融资融券有 {dropped} 条记录因关键字段无效被丢弃。")
    return NormalizedData(
        {"symbol": request.symbol, "items": items, "note": note},
        degraded=dropped > 0,
        warnings=tuple(warnings),
    )


def _own_rows(
    raw: object, symbol: str, code_field: str, label: str
) -> list[JsonRecord]:
    """The report's rows, refusing any that belong to another stock.

    Each report filters on its own code column. Were the filter ever ignored,
    the gateway would hand back a page of the whole market, and this stock's
    rows would likely not be on it: read naively, that is "no 解禁" for a
    stock that has them.
    """

    if not isinstance(raw, list):
        raise UpstreamUnavailable(
            f"东方财富{label}响应不是列表。", error_category="invalid_response"
        )
    rows = [row for row in raw if isinstance(row, dict)]
    code = symbol_code(symbol)
    if any(first_text(row, code_field) != code for row in rows):
        raise UpstreamUnavailable(
            f"东方财富{label}返回了别的股票的记录，查询条件没有生效。",
            error_category="invalid_response",
        )
    return rows


def _require_some(
    rows: list[JsonRecord], parsed: list[dict[str, object]], label: str
) -> int:
    if rows and not parsed:
        raise UpstreamUnavailable(
            f"东方财富{label}响应中的关键字段无效。",
            error_category="invalid_response",
        )
    return len(rows) - len(parsed)


def _lift_batch(row: JsonRecord) -> dict[str, object] | None:
    day = normalize_date(row.get("FREE_DATE"))
    shares = _wan(row, "CURRENT_FREE_SHARES")
    if day is None or shares is None:
        return None
    return {
        "date": day,
        "type": first_text(row, "FREE_SHARES_TYPE") or None,
        "shares": round(shares),
        "tradable_shares": _whole(_wan(row, "ABLE_FREE_SHARES")),
        "total_ratio_percent": _fraction_percent(row, "TOTAL_RATIO"),
        "float_ratio_percent": _fraction_percent(row, "FREE_RATIO"),
        "market_value": _cents(_wan(row, "LIFT_MARKET_CAP")),
    }


def _margin_day(row: JsonRecord) -> dict[str, object] | None:
    day = normalize_date(row.get("DATE"))
    balance = first_number(row, "RZYE")
    if day is None or balance is None:
        return None
    buy = first_number(row, "RZMRE")
    repay = first_number(row, "RZCHE")
    ratio = first_number(row, "RZYEZB")
    return {
        "date": day,
        "financing_balance": balance,
        "financing_buy": buy,
        "financing_repay": repay,
        "financing_net_buy": (
            buy - repay
            if buy is not None and repay is not None
            else first_number(row, "RZJME")
        ),
        "short_shares": first_number(row, "RQYL"),
        "short_balance": first_number(row, "RQYE"),
        "total_balance": first_number(row, "RZRQYE"),
        "financing_balance_ratio": None if ratio is None else round(ratio, 4),
        "close": first_number(row, "SPJ"),
    }


def _lift_note(
    has_rows: bool,
    as_of: date,
    horizon: date,
    upcoming: list[dict[str, object]],
    past: list[dict[str, object]],
    later: list[dict[str, object]],
) -> str | None:
    """Say what the window leaves out, so "none in 90 days" is not read as
    "none ahead": 600487 on 2026-09-29 had nothing until 2027-06-21. And say
    so when a batch starts trading today."""

    if not has_rows:
        return NO_LIFT_RECORDS
    if upcoming:
        if _day(upcoming[0]) == as_of:
            return f"今天（{as_of.isoformat()}）有一批解禁，是 upcoming 的第一条。"
        return None
    window = f"今天起 {LIFT_WINDOW_DAYS} 天内（至 {horizon.isoformat()}）没有解禁"
    if not past:
        window += "，也没有已经解禁的批次"
    if not later:
        return f"{window}，之后也没有已排定的批次。"
    after = later[0]
    ratio = after["total_ratio_percent"]
    share = f"，占总股本 {ratio}%" if ratio is not None else ""
    return (
        f"{window}；之后最近一批在 {after['date']}（{after['type'] or '类型未注明'}，"
        f"{after['shares']:,} 股{share}），共 {len(later)} 批已排定。"
    )


def _day(batch: dict[str, object]) -> date:
    return date.fromisoformat(str(batch["date"]))


def _wan(row: JsonRecord, key: str) -> float | None:
    value = first_number(row, key)
    return None if value is None else value * 10_000


def _whole(value: float | None) -> int | None:
    return None if value is None else round(value)


def _cents(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _fraction_percent(row: JsonRecord, key: str) -> float | None:
    value = first_number(row, key)
    return None if value is None else round(value * 100, 4)


__all__ = ["normalize_lift_schedule", "normalize_margin_detail"]
