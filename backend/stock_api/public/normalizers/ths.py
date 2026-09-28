"""同花顺 standardizers: 涨停归因, its 涨停揭秘 fallback, and the 人气热榜.

涨停归因 (getharden) is a list 同花顺's editors keep by hand: every stock that
closed at the limit, ST names included, each with a 题材 line such as
「拟收购界面财联社+上海国资+“华”字辈」. Two of its units are not what the
field names suggest. Both were checked on 2026-09-28 against 001330: 410456 手
× 100 × 6.36 元 ≈ 26105 万元, and the fields said 26105 and 410456.

- ``chengjiaoe`` (成交额) is in 万元, not 元.
- ``chengjiaoliang`` (成交量) is in 手, not 股.

It has no board count and no seal time, and it comes in the order the editors
added the rows. So the rows are sorted here by 成交额: the day's
heaviest-traded limit-ups first.

涨停揭秘 (limit_up_pool) carries the same 题材 text: it matched getharden for 33
of 33 codes on 2026-09-28. It adds how each board was sealed, but it leaves ST
names out (35 rows in getharden that day, 33 here), and it has no 成交额. Its
seal times are Unix seconds, as strings.

Its 「N天M板」 counts boards in a window that starts on a board and ends today,
gaps allowed. When N == M the streak is M. When N > M it is broken somewhere:
N天2板 is exactly one board today, but from M = 3 on it can be anything from 1
to M − 1 — 4天3板 is L L _ L or L _ L L. East Money had 1 for every such row
on 2026-09-28 (江淮汽车 4天3板 was L L _ L), so 1 is what is counted, as a
lower bound that the results name.

The 人气热榜 has no date at all. It is 同花顺's popularity ranking at the moment
of asking. Its two concept tags per stock are board names, not the reason the
stock is on the list.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.stock_api.public.contracts import HotListRequest, LimitReasonsRequest
from backend.stock_api.public.errors import NoStockData, UpstreamUnavailable
from backend.stock_api.public.normalizers.common import (
    JsonRecord,
    NormalizedData,
    as_record,
    canonical_symbol,
    clean_text,
    first_number,
    first_text,
    normalize_date,
    number,
)

HARDEN_BASIS = (
    "同花顺涨停归因：只含当日收盘涨停的股票（含 ST），题材由同花顺编辑手工标注，"
    "盘中名单可能还不全；按成交额从大到小排列。"
)
REASONS_FALLBACK = (
    "同花顺涨停归因没有取到，改用同花顺涨停揭秘：题材文字同源，但不含 ST 股，"
    "也没有成交额和成交量；按连板高度、首次封板时间排列。"
)
HOT_LIST_BASIS = (
    "同花顺人气热榜（日榜）：heat 是同花顺的人气值，没有单位，只能在榜内比较；"
    "tags 是同花顺的概念板块名，不是上榜原因；streak_label 是它的人气标签"
    "（N天M板、首板涨停或持续上榜）。"
)
HOT_LIST_SNAPSHOT_NOTE = "同花顺人气热榜为请求时刻的快照"

_MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
_CODE = re.compile(r"\d{6}")
_STREAK = re.compile(r"(\d+)天(\d+)板")
_REASON_MAXIMUM = 100


def ths_streak(row: JsonRecord) -> tuple[int, int] | None:
    """(N days, M boards) from a 涨停揭秘 row's 「N天M板」, or None.

    ``high_days_value`` packs it as ``(M << 16) | N`` (196612 is 4天3板); the
    ``high_days`` label is read when it is missing. It can be null even for
    a real 连板: 603396 was a 3连板 at East Money on 09-28 and null here.
    """

    packed = _whole(row.get("high_days_value"))
    if packed:
        boards, days = packed >> 16, packed & 0xFFFF
    else:
        label = first_text(row, "high_days")
        if label == "首板":
            return 1, 1
        match = _STREAK.fullmatch(label)
        if match is None:
            return None
        days, boards = int(match.group(1)), int(match.group(2))
    if boards <= 0 or days <= 0:
        return None
    return days, boards


def ths_board_count(row: JsonRecord) -> int | None:
    """Consecutive boards today: M when N == M, else 1 — exact for N天2板, a
    lower bound from M = 3 on (see `ths_board_ceiling`)."""

    streak = ths_streak(row)
    if streak is None:
        return None
    days, boards = streak
    return boards if boards == days else 1


def ths_board_ceiling(row: JsonRecord) -> int | None:
    """The most boards today's streak can be when `ths_board_count` is only a
    lower bound — M − 1 for N > M ≥ 3 — else None."""

    streak = ths_streak(row)
    if streak is None:
        return None
    days, boards = streak
    return boards - 1 if days > boards >= 3 else None


def normalize_ths_harden(raw: object, request: LimitReasonsRequest) -> NormalizedData:
    """getharden, with anything short of the requested day's list handed on.

    An empty list or another day's list raises NoStockData, which passes the
    request to 涨停揭秘 instead of answering "no reasons". getharden is written
    by hand and fills in after the fact, while 涨停揭秘 is live: during the
    session the first can be empty while the second already lists the day's
    limit-ups. When both have nothing, the answer is 涨停揭秘's empty result,
    with its note.
    """

    envelope = as_record(raw)
    if envelope is None:
        raise UpstreamUnavailable("同花顺涨停归因响应不是对象。")
    rows = _rows(envelope.get("data"), "同花顺涨停归因")
    day = normalize_date(envelope.get("date"))
    if day is None and rows:
        day = normalize_date(rows[0].get("date"))
    if day != request.trade_date:
        raise NoStockData(
            f"同花顺涨停归因返回的是 {day or '未标日期'} 的名单，"
            f"不是 {request.trade_date}。"
        )
    if not rows:
        raise NoStockData(f"同花顺涨停归因还没有 {request.trade_date} 的名单。")
    items = [item for row in rows if (item := _harden_item(row))]
    if not items:
        raise UpstreamUnavailable("同花顺涨停归因响应中的证券代码全部无效。")
    items.sort(key=_by_amount)
    return NormalizedData(
        {
            "trade_date": request.trade_date,
            "count": len(items),
            "items": items[: request.limit],
        },
        warnings=(HARDEN_BASIS,),
    )


def normalize_ths_limit_reasons(
    raw: object, request: LimitReasonsRequest
) -> NormalizedData:
    """涨停揭秘 standing in for getharden; the last candidate, so an empty day
    is an empty success with a note."""

    data = as_record(raw)
    if data is None:
        raise UpstreamUnavailable("同花顺涨停揭秘响应不是对象。")
    rows = _rows(data.get("info"), "同花顺涨停揭秘")
    day = normalize_date(data.get("date"))
    if day is not None and day != request.trade_date:
        return _empty_reasons(
            request,
            f"同花顺涨停揭秘返回的是 {day} 的名单，不是 {request.trade_date}；"
            "该日可能不是交易日，或者数据还没有出来。",
        )
    if not rows:
        return _empty_reasons(
            request,
            f"同花顺涨停揭秘没有 {request.trade_date} 的涨停股：该日可能不是交易日，"
            "或者数据还没有出来。",
        )
    ranked = [(row, item) for row in rows if (item := _pool_item(row))]
    if not ranked:
        raise UpstreamUnavailable("同花顺涨停揭秘响应中的证券代码全部无效。")
    ranked.sort(key=_by_streak)
    items = [item for _, item in ranked]
    return NormalizedData(
        {
            "trade_date": request.trade_date,
            "count": len(items),
            "items": items[: request.limit],
        },
        degraded=True,
        warnings=(REASONS_FALLBACK,),
    )


def normalize_ths_hot_list(raw: object, request: HotListRequest) -> NormalizedData:
    data = as_record(raw)
    if data is None:
        raise UpstreamUnavailable("同花顺人气热榜响应不是对象。")
    rows = data.get("stock_list")
    if not isinstance(rows, list):
        raise UpstreamUnavailable("同花顺人气热榜响应缺少 stock_list。")
    if not rows:
        # It is always 100 rows; none at all is the source failing, not a
        # quiet day.
        raise NoStockData("同花顺人气热榜为空。")
    items = [
        item
        for position, row in enumerate(rows, start=1)
        if isinstance(row, dict) and (item := _hot_item(row, position))
    ]
    if not items:
        raise UpstreamUnavailable("同花顺人气热榜响应中的证券代码全部无效。")
    items.sort(key=lambda item: _integer(item["rank"]))
    return NormalizedData(
        {
            "fetched_at_note": HOT_LIST_SNAPSHOT_NOTE,
            "count": len(items),
            "items": items[: request.limit],
        },
        warnings=(HOT_LIST_BASIS,),
    )


def _rows(value: object, label: str) -> list[JsonRecord]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise UpstreamUnavailable(f"{label}响应中的列表格式无效。")
    return [row for row in value if isinstance(row, dict)]


def _identity(row: JsonRecord) -> dict[str, object] | None:
    # Whole-market rows keep their code and name even where canonical_symbol
    # has no form for them (北交所 920xxx and the like).
    code = first_text(row, "code")
    if not _CODE.fullmatch(code):
        return None
    return {
        "code": code,
        "symbol": canonical_symbol(code),
        "name": first_text(row, "name") or None,
    }


def _reasons(row: JsonRecord, key: str) -> dict[str, object]:
    reason_text, _ = clean_text(row.get(key), _REASON_MAXIMUM)
    return {
        "reasons": [part.strip() for part in reason_text.split("+") if part.strip()],
        "reason_text": reason_text or None,
    }


def _harden_item(row: JsonRecord) -> dict[str, object] | None:
    identity = _identity(row)
    if identity is None:
        return None
    return {
        **identity,
        **_reasons(row, "reason"),
        "price": first_number(row, "close"),
        "change_percent": first_number(row, "zhangfu"),
        "turnover_rate": first_number(row, "huanshou"),
        "amount": _scaled(row, "chengjiaoe", 10_000),
        "volume_shares": _scaled(row, "chengjiaoliang", 100),
    }


def _pool_item(row: JsonRecord) -> dict[str, object] | None:
    identity = _identity(row)
    if identity is None:
        return None
    seal_fund = first_number(row, "order_amount")
    broken = first_number(row, "open_num")
    return {
        **identity,
        **_reasons(row, "reason_type"),
        "price": first_number(row, "latest"),
        "change_percent": first_number(row, "change_rate"),
        "turnover_rate": first_number(row, "turnover_rate"),
        "amount": None,
        "volume_shares": None,
        # 「首板」「2天2板」「4天3板」. It can be null even for a real 连板:
        # 603396 was a 3连板 at East Money on 09-28 and null here.
        "streak": first_text(row, "high_days") or None,
        "board_type": first_text(row, "limit_up_type") or None,
        "first_seal_time": _clock(row.get("first_limit_up_time")),
        "last_seal_time": _clock(row.get("last_limit_up_time")),
        # null when the board never opened: the 12 such rows on 09-28 were
        # all FIRST_LIMIT, never resealed.
        "broken_times": 0 if broken is None else int(broken),
        "seal_fund": None if seal_fund is None else round(seal_fund),
    }


def _hot_item(row: JsonRecord, position: int) -> dict[str, object] | None:
    identity = _identity(row)
    if identity is None:
        return None
    tag = as_record(row.get("tag")) or {}
    concepts = tag.get("concept_tag")
    heat = first_number(row, "rate")
    rank = first_number(row, "order")
    return {
        "rank": position if rank is None else int(rank),
        **identity,
        "change_percent": first_number(row, "rise_and_fall"),
        "tags": [
            label
            for value in (concepts if isinstance(concepts, list) else [])
            if (label := clean_text(value, 40)[0])
        ],
        # The key is absent, not empty, on rows without a label.
        "streak_label": first_text(tag, "popularity_tag") or None,
        # A string upstream, e.g. "6191304".
        "heat": None if heat is None else int(heat),
    }


def _scaled(row: JsonRecord, key: str, factor: int) -> int | None:
    value = first_number(row, key)
    return None if value is None else round(value * factor)


def _clock(value: object) -> str | None:
    """Unix seconds as a string, e.g. "1790559022", to Shanghai HH:MM:SS.

    Zoned explicitly: the host's own zone is not the exchange's.
    """

    seconds = number(value)
    if seconds is None or seconds <= 0:
        return None
    try:
        moment = datetime.fromtimestamp(seconds, tz=_MARKET_TIMEZONE)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.strftime("%H:%M:%S")


def _integer(value: object) -> int:
    return value if isinstance(value, int) else 0


def _whole(value: object) -> int | None:
    parsed = number(value)
    if parsed is None or (isinstance(parsed, float) and not parsed.is_integer()):
        return None
    return int(parsed)


def _by_amount(item: dict[str, object]) -> tuple[bool, float]:
    amount = item.get("amount")
    if isinstance(amount, (int, float)):
        return False, -float(amount)
    return True, 0.0


def _by_streak(pair: tuple[JsonRecord, dict[str, object]]) -> tuple[int, bool, str]:
    """Consecutive boards first, counted as the 涨停池 counts them — 4天3板 is
    one board today, not three — then who sealed first. An unlabelled row
    goes after the 首板 rows."""

    row, item = pair
    sealed = item.get("first_seal_time")
    return (
        -(ths_board_count(row) or 0),
        not isinstance(sealed, str),
        sealed if isinstance(sealed, str) else "",
    )


def _empty_reasons(request: LimitReasonsRequest, note: str) -> NormalizedData:
    return NormalizedData(
        {
            "trade_date": request.trade_date,
            "count": 0,
            "items": [],
            "note": note,
        },
        degraded=True,
        warnings=(REASONS_FALLBACK,),
    )


__all__ = [
    "HARDEN_BASIS",
    "HOT_LIST_BASIS",
    "HOT_LIST_SNAPSHOT_NOTE",
    "REASONS_FALLBACK",
    "normalize_ths_harden",
    "normalize_ths_hot_list",
    "normalize_ths_limit_reasons",
    "ths_board_ceiling",
    "ths_board_count",
    "ths_streak",
]
