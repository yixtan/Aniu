"""涨跌停池 standardizers: East Money's four pools, and 同花顺's as a stand-in.

What the pools hold, as they read after the close of 2026-09-28. During the
session they hold the same things as of the moment of asking — sealed now,
broken now — so the bases say "at fetch time" and the tool marks today's
answers taken before the close.

- 涨停池 (ZT): closed sealed at the limit. A stock that opened and resealed
  stays here with ``zbc`` > 0 — 21 of that day's 33 did — and is not in the
  炸板池, so 炸板率 counts only the stocks that ended the day unsealed.
- 炸板池 (ZB): touched the limit, closed below it (11). It has no ``lbc``,
  so the height and the ladder come from the 涨停池 alone.
- 跌停池 (DT): closed at the limit-down price (56). Its ``days`` and ``oc``
  are the limit-down counterparts of ``lbc`` and ``zbc``.
- 昨日涨停池 (YZT): the previous trading day's 涨停 stocks with today's
  quotes (52, from 09-24 across the 09-25..27 break); ``ztp`` is TODAY's
  limit price and ``ylbc`` yesterday's board count.

Units as sent: ``p`` and ``ztp`` are 元 × 1000; ``amount``, ``ltsz`` and
``fund`` are 元; ``zdp`` and ``hs`` are percent; times are HHMMSS integers
(92500 is the 09:25 call auction). ``m`` cannot tell Shenzhen from 北交所 —
920748 came back m=0 — so the exchange comes from the code, and a code
`canonical_symbol` refuses keeps its ``code`` and ``name`` with no symbol.
DT's ``fba`` is left out: documented as 板上成交额, it exceeded the whole
day's turnover on 8 of 56 rows (603230: 13.2 亿 against 2.6 亿).

None of the four holds ST stocks. 同花顺's 涨停归因 listed ST万邦 002082 and
*ST华幸 600340 as 涨停 on 09-28; East Money's 涨停池 had neither, nor did the
56-row 跌停池 or any other pool hold a single ST name. So its counts leave
them out, and the bases say so.

``tc`` is the pool's own total. The counts use it, not the rows sent:
``pagesize`` 10000 held every row on 09-28, but a cap nobody has seen yet
would otherwise shrink every count and the 炸板率 without a word.

晋级率 counts the 昨日涨停池 stocks whose price equals today's limit price,
not those up 9.8% or more as the a-stock-data document has it. On 2026-09-28
its rule also counted 301699 洛轴股份, a 创业板 stock up 14.46% against a 20%
limit, and gave 8/52 = 15.4% where 7/52 = 13.5% sealed again. (Main-board ST
stocks have a 10% limit, like the rest of the main board: *ST华幸 went 1.17 →
1.29 that day. They are simply not in the pools.)

同花顺's 涨停揭秘 stands in when push2ex will not answer. Its day totals give
the 涨停, 炸板 and 跌停 counts in one request and agreed with East Money's on
that day (33, 11, 56); its rows give an approximate ladder. It has no
昨日涨停池 and no 北交所, and its ``high_days`` was null for 603396, an East
Money 3连板, so those results say what they lack.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.stock_api.public.contracts import LimitPoolRequest, LimitSummaryRequest
from backend.stock_api.public.errors import UpstreamUnavailable
from backend.stock_api.public.normalizers.common import (
    JsonRecord,
    NormalizedData,
    as_record,
    canonical_symbol,
    normalize_date,
    number,
    text,
)
from backend.stock_api.public.normalizers.ths import (
    ths_board_ceiling,
    ths_board_count,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
SUMMARY_TOP = 10

POOL_LABELS = {
    "limit_up": "涨停池",
    "broken": "炸板池",
    "limit_down": "跌停池",
    "previous_limit_up": "昨日涨停池",
}
POOL_BASIS = {
    "limit_up": (
        "东方财富涨停池：取数时封在涨停价的股票（盘中是那一刻的状态，收盘后即"
        "收盘状态），盘中开板后回封的也在内（broken_times 是开板次数）；不含 ST "
        "股。board_count 是连板数，seal_fund 是取数时的封单金额（元），streak "
        "是近 N 天 M 板；按连板数从高到低、首次封板时间先后排列。"
    ),
    "broken": (
        "东方财富炸板池：当天触及过涨停、取数时没封住的股票（收盘后即收盘没"
        "封住）；不含 ST 股。limit_price 是当天的涨停价，broken_times 是开板"
        "次数；按首次封板时间先后排列。"
    ),
    "limit_down": (
        "东方财富跌停池：取数时封在跌停价的股票（收盘后即收盘状态）；不含 ST "
        "股。board_count 是连续跌停天数，broken_times 是跌停板被打开的次数，"
        "seal_fund 是取数时的卖方封单金额（元）；按封单金额从大到小排列。"
    ),
    "previous_limit_up": (
        "东方财富昨日涨停池：上一个交易日涨停的股票今天的表现；不含 ST 股。"
        "price 等于 limit_price（今天的涨停价）就是今天再次涨停，"
        "yesterday_board_count 是昨天的连板数；按今天涨跌幅从高到低排列，"
        "超过 limit 时保留涨得最多和跌得最多的两头，截掉中间。"
    ),
}
SUMMARY_BASIS = (
    "东方财富口径：涨停、炸板、跌停池都不含 ST 股（同花顺涨停归因含 ST，"
    "所以家数可能比它少）；炸板率 = 炸板 ÷（涨停 + 炸板），炸板只算取数时没"
    "封住的（盘中是那一刻，收盘后即收盘）；连板数和梯队取自涨停池；晋级率 = "
    "昨日涨停股现价等于今天涨停价的家数 ÷ 昨日涨停家数（不按涨幅 ≥ 9.8% 算，"
    "创业板、科创板和北交所的涨停幅度不同）；昨日涨停今日平均涨幅是它们今天"
    "涨跌幅的算术平均。"
)
THS_POOL_BASIS = (
    "同花顺口径（东方财富涨停池不可用时的替代）：不含 ST 股和北交所；"
    "board_count 由同花顺的「N天M板」推算：N 等于 M 是 M 连板，N天2板 是 1 "
    "板；M ≥ 3 而 N 大于 M 时（中间断过板）按 1 板计，这是下限，实际可能到 "
    "M−1 板，原标签见 streak；broken_times 是同花顺数的开板次数，和东方财富的"
    "不完全一致；没有成交额和行业。"
)
THS_SUMMARY_BASIS = (
    "同花顺口径（东方财富涨跌停池不可用时的替代）：涨停、炸板、跌停家数取自"
    "同花顺的当日汇总，不含 ST 股和北交所；炸板率 = 炸板 ÷（涨停 + 炸板）；"
    "连板梯队由同花顺的「N天M板」推算：N 等于 M 是 M 连板，否则按 1 板计"
    "（M ≥ 3 时这是下限）。"
)
THS_NO_YESTERDAY = "替代数据没有昨日涨停池，晋级率和昨日涨停今日表现为空。"

_BOARD_FIELDS = {"limit_up": "lbc", "limit_down": "days"}
_BROKEN_FIELDS = {"limit_up": "zbc", "broken": "zbc", "limit_down": "oc"}


def normalize_limit_pool(raw: object, request: LimitPoolRequest) -> NormalizedData:
    pool = _read_pool(raw, request.pool, request.trade_date)
    items = _items(pool.rows, request.pool)
    data: dict[str, object] = {
        "trade_date": request.trade_date,
        "pool": request.pool,
        "count": pool.count,
    }
    if pool.note:
        data["note"] = pool.note
    data["items"] = (
        _both_ends(items, request.limit)
        if request.pool == "previous_limit_up"
        else items[: request.limit]
    )
    warnings = [POOL_BASIS[request.pool]]
    if pool.short:
        warnings.append(
            f"东方财富只返回了 {len(pool.rows)} / {pool.count} 只"
            f"{POOL_LABELS[request.pool]}的明细，count 按它给的总数，items 只有"
            "这些。"
        )
    return NormalizedData(data, warnings=tuple(warnings))


def normalize_limit_summary(
    raw: object, request: LimitSummaryRequest
) -> NormalizedData:
    """Raw is `limit_pools_all`'s dict of the four pools for one day."""

    record = as_record(raw)
    if record is None:
        raise UpstreamUnavailable("东方财富涨跌停池响应无效。")
    reads = {
        pool: _read_pool(record.get(pool), pool, request.trade_date)
        for pool in POOL_LABELS
    }
    elsewhere = [read.note for read in reads.values() if read.other_day]
    if elsewhere and len(elsewhere) < len(reads):
        # A 炸板率 from two different days is wrong without looking wrong.
        raise UpstreamUnavailable("东方财富涨跌停池各池子的日期不一致。")
    counts = {pool: read.count for pool, read in reads.items()}
    limit_up = _items(reads["limit_up"].rows, "limit_up")
    previous_read = reads["previous_limit_up"]
    previous = previous_read.rows
    # Sent best first (zs:desc), so a cut page is not a sample: a 晋级率 from
    # it would be too high.
    promoted: int | None = None
    promotion: float | None = None
    average: float | None = None
    if not previous_read.short:
        promoted = sum(1 for row in previous if _sealed_again(row))
        changes = [
            value for row in previous if (value := number(row.get("zdp"))) is not None
        ]
        promotion = round(promoted / len(previous) * 100, 1) if previous else None
        average = round(sum(changes) / len(changes), 2) if changes else None
    boards = [value for item in limit_up if (value := _int_of(item, "board_count"))]
    denominator = counts["limit_up"] + counts["broken"]
    data: dict[str, object] = {
        "trade_date": request.trade_date,
        "limit_up_count": counts["limit_up"],
        "broken_count": counts["broken"],
        "limit_down_count": counts["limit_down"],
        "previous_limit_up_count": counts["previous_limit_up"],
        "break_rate_percent": (
            round(counts["broken"] / denominator * 100, 1) if denominator else 0.0
        ),
        "max_board_count": max(boards, default=0),
        "ladder": _ladder(boards),
        "promoted_count": promoted,
        "promotion_rate_percent": promotion,
        "previous_limit_up_avg_change_percent": average,
    }
    note = _summary_note(request.trade_date, counts, elsewhere)
    if note:
        data["note"] = note
    data["items"] = [_leader(item) for item in limit_up[:SUMMARY_TOP]]
    warnings = [SUMMARY_BASIS]
    short = [pool for pool, read in reads.items() if read.short]
    if short:
        detail = "、".join(
            f"{POOL_LABELS[pool]} {len(reads[pool].rows)} / {counts[pool]}"
            for pool in short
        )
        warning = f"东方财富只返回了部分明细（{detail}），家数和炸板率按它给的总数。"
        if "limit_up" in short:
            warning += "连板梯队和高度只按返回的涨停股算，可能偏低。"
        if previous_read.short:
            warning += "晋级率和昨日涨停今日表现没有算：返回的只是涨幅靠前的那些。"
        warnings.append(warning)
    return NormalizedData(data, warnings=tuple(warnings))


def normalize_ths_limit_pool(raw: object, request: LimitPoolRequest) -> NormalizedData:
    if request.pool != "limit_up":
        raise UpstreamUnavailable("同花顺涨停揭秘只有涨停池。", retryable=False)
    pool = _read_ths(raw, request.trade_date)
    items = [item for row in pool.rows if (item := _ths_item(row))]
    items.sort(key=_limit_up_order)
    data: dict[str, object] = {
        "trade_date": request.trade_date,
        "pool": request.pool,
        "count": pool.limit_up if pool.limit_up is not None else len(pool.rows),
    }
    if not items:
        data["note"] = pool.note or f"同花顺 {request.trade_date} 的涨停池没有股票。"
    data["items"] = items[: request.limit]
    warnings = (THS_POOL_BASIS, *pool.warnings())
    return NormalizedData(data, degraded=True, warnings=warnings)


def normalize_ths_limit_summary(
    raw: object, request: LimitSummaryRequest
) -> NormalizedData:
    pool = _read_ths(raw, request.trade_date)
    if pool.note is None and (pool.limit_up is None or pool.broken is None):
        raise UpstreamUnavailable("同花顺涨停揭秘响应缺少涨跌停家数汇总。")
    limit_up = pool.limit_up or 0
    broken = pool.broken or 0
    items = [item for row in pool.rows if (item := _ths_item(row))]
    items.sort(key=_limit_up_order)
    boards = [value for item in items if (value := _int_of(item, "board_count"))]
    unknown = len(items) - len(boards)
    ceilings = [
        ceiling
        for row in pool.rows
        if _code(row.get("code")) and (ceiling := ths_board_ceiling(row))
    ]
    data: dict[str, object] = {
        "trade_date": request.trade_date,
        "limit_up_count": limit_up,
        "broken_count": broken,
        "limit_down_count": pool.limit_down,
        "previous_limit_up_count": None,
        "break_rate_percent": (
            round(broken / (limit_up + broken) * 100, 1) if limit_up + broken else 0.0
        ),
        "max_board_count": max(boards, default=0),
        "ladder": _ladder(boards),
        "promoted_count": None,
        "promotion_rate_percent": None,
        "previous_limit_up_avg_change_percent": None,
    }
    if pool.note:
        data["note"] = pool.note
    elif not limit_up and not broken and not pool.limit_down:
        data["note"] = (
            f"同花顺 {request.trade_date} 没有涨跌停股：可能不是交易日，或还没开盘。"
        )
    data["items"] = [_leader(item) for item in items[:SUMMARY_TOP]]
    warnings = [THS_SUMMARY_BASIS, THS_NO_YESTERDAY, *pool.warnings()]
    if unknown:
        warnings.append(
            f"有 {unknown} 只涨停股同花顺没有给出几天几板，没有计入梯队和连板高度。"
        )
    if ceilings:
        highest = max(boards, default=0)
        warning = (
            f"有 {len(ceilings)} 只涨停股是「N天M板」且 M ≥ 3（中间断过板），"
            "同花顺分不出现在连了几板，按 1 板计入梯队；梯队是下限。"
        )
        if max(ceilings) > highest:
            warning += (
                f"连板高度也可能不止 {highest} 板，其中最多可能到 {max(ceilings)} 板。"
            )
        warnings.append(warning)
    return NormalizedData(data, degraded=True, warnings=tuple(warnings))


# East Money pools


@dataclass(frozen=True, slots=True)
class _Pool:
    rows: list[JsonRecord]
    note: str | None = None
    """Why the pool is empty, when it is."""
    other_day: str | None = None
    """The day push2ex answered for, when it is not the day asked for."""
    total: int | None = None
    """``tc``, the pool's size as push2ex counts it."""

    @property
    def count(self) -> int:
        return max(self.total or 0, len(self.rows))

    @property
    def short(self) -> bool:
        """Fewer rows sent than ``tc`` says the pool holds."""

        return self.total is not None and self.total > len(self.rows)


def _read_pool(raw: object, pool: str, trade_date: str) -> _Pool:
    label = POOL_LABELS[pool]
    root = as_record(raw)
    if root is None:
        raise UpstreamUnavailable(f"东方财富{label}响应无效。")
    data = root.get("data")
    if data is None:
        # What push2ex sends for a day with no pool, and possibly for a
        # trading day whose pool is empty: the null has not been seen on one.
        return _Pool(
            [],
            f"东方财富没有返回 {trade_date} 的{label}：非交易日、还没开盘，"
            "或当天该池为 0 只。",
        )
    if not isinstance(data, dict):
        raise UpstreamUnavailable(f"东方财富{label}响应无效。")
    day = normalize_date(data.get("qdate"))
    if day is not None and day != trade_date:
        return _Pool(
            [],
            f"东方财富返回的是 {day} 的{label}，不是所问的 {trade_date}；按空处理。",
            day,
        )
    pool_rows = data.get("pool")
    if pool_rows is None and not number(data.get("tc")):
        pool_rows = []
    if not isinstance(pool_rows, list):
        raise UpstreamUnavailable(f"东方财富{label}响应缺少股票列表。")
    rows = [row for row in pool_rows if isinstance(row, dict)]
    if pool_rows and not any(_code(row.get("c")) for row in rows):
        raise UpstreamUnavailable(f"东方财富{label}响应中的股票记录无效。")
    total = _whole(data.get("tc"))
    return _Pool(
        rows,
        None if rows else f"{trade_date} 的{label}没有股票。",
        total=total if total is not None and total >= 0 else None,
    )


def _items(rows: list[JsonRecord], pool: str) -> list[dict[str, object]]:
    items = [item for row in rows if (item := _pool_item(row, pool))]
    items.sort(key=_ORDERS[pool])
    return items


def _pool_item(row: JsonRecord, pool: str) -> dict[str, object] | None:
    code = _code(row.get("c"))
    if code is None:
        return None
    board_field = _BOARD_FIELDS.get(pool)
    broken_field = _BROKEN_FIELDS.get(pool)
    return {
        "code": code,
        "symbol": canonical_symbol(code),
        "name": text(row.get("n")) or None,
        "price": _thousandths(row.get("p")),
        "change_percent": _rounded(row.get("zdp"), 2),
        "amount": _yuan(row.get("amount")),
        "float_market_value": _yuan(row.get("ltsz")),
        "turnover_rate": _rounded(row.get("hs"), 2),
        "board_count": _whole(row.get(board_field)) if board_field else None,
        "streak": _streak(row.get("zttj")),
        "seal_fund": _yuan(row.get("fund")),
        "first_seal_time": _clock(row.get("fbt")),
        "last_seal_time": _clock(row.get("lbt")),
        "broken_times": _whole(row.get(broken_field)) if broken_field else None,
        "limit_price": _thousandths(row.get("ztp")),
        "yesterday_board_count": _whole(row.get("ylbc")),
        "industry": text(row.get("hybk")) or None,
    }


def _sealed_again(row: JsonRecord) -> bool:
    price = number(row.get("p"))
    limit = number(row.get("ztp"))
    return price is not None and limit is not None and limit > 0 and price == limit


def _summary_note(
    trade_date: str, counts: dict[str, int], elsewhere: list[str | None]
) -> str | None:
    if elsewhere:
        return elsewhere[0]
    today = counts["limit_up"] + counts["broken"] + counts["limit_down"]
    if today:
        return None
    if not counts["previous_limit_up"]:
        return f"东方财富没有 {trade_date} 的涨跌停数据：可能不是交易日，或还没开盘。"
    return (
        f"{trade_date} 的涨停、炸板、跌停池都是空的；如果还没到 09:25 集合竞价"
        "结束，是还没开盘，晋级率和昨日涨停今日表现也还不能看。"
    )


def _streak(value: object) -> str | None:
    record = as_record(value)
    if record is None:
        return None
    days = _whole(record.get("days"))
    boards = _whole(record.get("ct"))
    if not days or not boards:
        # {0, 0}: no 涨停 in the window, 8 of the 11 炸板 on 2026-09-28.
        return None
    return "首板" if days == boards == 1 else f"{days}天{boards}板"


def _clock(value: object) -> str | None:
    whole = _whole(value)
    if whole is None or not 0 < whole <= 235959:
        return None
    digits = f"{whole:06d}"
    hours, minutes, seconds = digits[:2], digits[2:4], digits[4:]
    if int(minutes) > 59 or int(seconds) > 59:
        return None
    return f"{hours}:{minutes}:{seconds}"


# 同花顺 涨停揭秘


@dataclass(frozen=True, slots=True)
class _ThsPool:
    rows: list[JsonRecord]
    limit_up: int | None
    broken: int | None
    limit_down: int | None
    total: int | None
    note: str | None = None
    """Why there is nothing to read, when there is not."""

    @classmethod
    def empty(cls, note: str) -> _ThsPool:
        return cls([], 0, 0, 0, 0, note)

    def warnings(self) -> tuple[str, ...]:
        if self.total is not None and self.total > len(self.rows):
            # Its pages come latest seal first, so the rows past the last
            # page fetched are the earliest sealed.
            return (
                f"同花顺只返回了 {len(self.rows)} / {self.total} 只涨停股，"
                "明细和梯队只按这些计算；缺的是封板最早的那些（多为一字板和"
                "高位连板），连板高度和梯队可能偏低。",
            )
        return ()


def _read_ths(raw: object, trade_date: str) -> _ThsPool:
    """Read `ThsAdapter.limit_up_pool`'s raw: the response's ``data`` as sent.

    ``date`` is the day asked for; ``trade_status`` describes the moment of
    the request (未开盘 at 01:45), not that day, so it is not read.
    """

    data = as_record(raw)
    if data is None:
        raise UpstreamUnavailable("同花顺涨停揭秘响应无效。")
    if not data:
        # The adapter hands ``data: null`` over as {}. Not seen yet; read as
        # "nothing for that day", the way push2ex's null is read.
        return _ThsPool.empty(
            f"同花顺没有 {trade_date} 的涨停揭秘：可能不是交易日，或还没开盘。"
        )
    day = normalize_date(data.get("date"))
    if day is not None and day != trade_date:
        return _ThsPool.empty(
            f"同花顺返回的是 {day} 的涨停揭秘，不是所问的 {trade_date}；按空处理。"
        )
    info = data.get("info")
    if info is None:
        info = []
    if not isinstance(info, list):
        raise UpstreamUnavailable("同花顺涨停揭秘响应缺少股票列表。")
    rows = [row for row in info if isinstance(row, dict)]
    if rows and not any(_code(row.get("code")) for row in rows):
        raise UpstreamUnavailable("同花顺涨停揭秘响应中的股票记录无效。")
    up_today = _field(data, "limit_up_count", "today")
    limit_up = _whole(up_today.get("num"))
    broken = _whole(up_today.get("open_num"))
    touched = _whole(up_today.get("history_num"))
    if broken is None and limit_up is not None and touched is not None:
        broken = touched - limit_up
    return _ThsPool(
        rows,
        limit_up,
        broken,
        _whole(_field(data, "limit_down_count", "today").get("num")),
        _whole(_field(data, "page").get("total")),
    )


def _field(record: JsonRecord, *keys: str) -> JsonRecord:
    for key in keys:
        record = as_record(record.get(key)) or {}
    return record


def _ths_item(row: JsonRecord) -> dict[str, object] | None:
    code = _code(row.get("code"))
    if code is None:
        return None
    opened = row.get("open_num")
    # null, not 0, when the board never opened (12 of 33 on 2026-09-28).
    never_opened = opened is None and row.get("is_again_limit") in {0, "0"}
    return {
        "code": code,
        "symbol": canonical_symbol(code),
        "name": text(row.get("name")) or None,
        "price": _rounded(row.get("latest"), 3),
        "change_percent": _rounded(row.get("change_rate"), 2),
        "amount": None,
        "float_market_value": _yuan(row.get("currency_value")),
        "turnover_rate": _rounded(row.get("turnover_rate"), 2),
        "board_count": ths_board_count(row),
        "streak": text(row.get("high_days")) or None,
        "seal_fund": _yuan(row.get("order_amount")),
        "first_seal_time": _epoch_clock(row.get("first_limit_up_time")),
        "last_seal_time": _epoch_clock(row.get("last_limit_up_time")),
        "broken_times": 0 if never_opened else _whole(opened),
        "limit_price": None,
        "yesterday_board_count": None,
        "industry": None,
    }


def _epoch_clock(value: object) -> str | None:
    """Epoch seconds, sent as a string; read in Beijing time, not the host's."""

    seconds = number(value)
    if seconds is None or seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, _SHANGHAI).strftime("%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return None


# Shared


def _leader(item: dict[str, object]) -> dict[str, object]:
    return {
        key: item.get(key)
        for key in ("code", "symbol", "name", "board_count", "streak", "industry")
    }


def _both_ends(items: list[dict[str, object]], limit: int) -> list[dict[str, object]]:
    """Both tails of a list ranked best to worst, the middle cut: the
    昨日涨停池's re-sealed names and its 大面 are what matter, not the flat."""

    if len(items) <= limit:
        return items
    head = (limit + 1) // 2
    return [*items[:head], *items[len(items) - (limit - head) :]]


def _ladder(boards: list[int]) -> list[dict[str, int]]:
    return [
        {"boards": level, "count": count}
        for level, count in sorted(Counter(boards).items(), reverse=True)
    ]


def _limit_up_order(item: dict[str, object]) -> tuple[int, bool, str]:
    seal = item.get("first_seal_time")
    return (
        -(_int_of(item, "board_count") or 0),
        not isinstance(seal, str),
        seal if isinstance(seal, str) else "",
    )


def _broken_order(item: dict[str, object]) -> tuple[bool, str]:
    seal = item.get("first_seal_time")
    return (not isinstance(seal, str), seal if isinstance(seal, str) else "")


def _descending(key: str) -> Callable[[dict[str, object]], tuple[bool, float]]:
    def order(item: dict[str, object]) -> tuple[bool, float]:
        value = item.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (False, -float(value))
        return (True, 0.0)

    return order


_ORDERS: dict[str, Callable[[dict[str, object]], tuple[object, ...]]] = {
    "limit_up": _limit_up_order,
    "broken": _broken_order,
    "limit_down": _descending("seal_fund"),
    "previous_limit_up": _descending("change_percent"),
}


def _code(value: object) -> str | None:
    code = text(value)
    return code if re.fullmatch(r"\d{6}", code) else None


def _int_of(item: dict[str, object], key: str) -> int | None:
    value = item.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _whole(value: object) -> int | None:
    parsed = number(value)
    if parsed is None or (isinstance(parsed, float) and not parsed.is_integer()):
        return None
    return int(parsed)


def _rounded(value: object, digits: int) -> float | None:
    parsed = number(value)
    return None if parsed is None else round(float(parsed), digits)


def _thousandths(value: object) -> float | None:
    parsed = number(value)
    return None if parsed is None else round(parsed / 1000, 3)


def _yuan(value: object) -> int | None:
    parsed = number(value)
    return None if parsed is None else round(parsed)


__all__ = [
    "POOL_BASIS",
    "SUMMARY_BASIS",
    "THS_NO_YESTERDAY",
    "THS_POOL_BASIS",
    "THS_SUMMARY_BASIS",
    "normalize_limit_pool",
    "normalize_limit_summary",
    "normalize_ths_limit_pool",
    "normalize_ths_limit_summary",
]
