"""涨跌停池 from 同花顺's 涨停揭秘, standing in when push2ex will not answer.

The fixture is 同花顺's answer for 2026-09-28, fetched after the close: 33
涨停 rows and its day totals (33 涨停, 11 炸板, 56 跌停), which agreed with East
Money's. It has no 昨日涨停池, no 北交所 and no ST names, and its 「N天M板」
cannot always say how many boards in a row a stock has now.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest

from backend.stock_api.public.contracts import LimitPoolRequest, LimitSummaryRequest
from backend.stock_api.public.errors import UpstreamUnavailable
from backend.stock_api.public.normalizers.limit_pools import (
    THS_NO_YESTERDAY,
    THS_POOL_BASIS,
    THS_SUMMARY_BASIS,
    normalize_ths_limit_pool,
    normalize_ths_limit_summary,
)

_FIXTURES = Path(__file__).parent / "fixtures"
DAY = "2026-09-28"


def ths_fixture() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(
            (_FIXTURES / "ths_pool_summary_20260928.json").read_text(encoding="utf-8")
        ),
    )


def items_of(data: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], data["items"])


def codes(data: dict[str, object]) -> list[object]:
    return [item["code"] for item in items_of(data)]


def test_the_tonghuashun_summary_uses_its_own_day_totals() -> None:
    result = normalize_ths_limit_summary(ths_fixture(), LimitSummaryRequest(DAY))

    data = result.data
    assert {key: value for key, value in data.items() if key != "items"} == {
        "trade_date": DAY,
        "limit_up_count": 33,
        "broken_count": 11,
        "limit_down_count": 56,
        "previous_limit_up_count": None,
        "break_rate_percent": 25.0,
        "max_board_count": 5,
        # 603396, an East Money 3连板, has no 几天几板 here.
        "ladder": [
            {"boards": 5, "count": 1},
            {"boards": 3, "count": 2},
            {"boards": 2, "count": 3},
            {"boards": 1, "count": 26},
        ],
        "promoted_count": None,
        "promotion_rate_percent": None,
        "previous_limit_up_avg_change_percent": None,
    }
    assert codes(data)[:3] == ["600825", "603949", "600802"]
    assert items_of(data)[0]["industry"] is None
    assert result.degraded is True
    assert result.warnings == (
        THS_SUMMARY_BASIS,
        THS_NO_YESTERDAY,
        "有 1 只涨停股同花顺没有给出几天几板，没有计入梯队和连板高度。",
        # 600418 江淮汽车 4天3板: L L _ L at East Money, but it could have been
        # L _ L L.
        "有 1 只涨停股是「N天M板」且 M ≥ 3（中间断过板），同花顺分不出现在连了"
        "几板，按 1 板计入梯队；梯队是下限。",
    )


def test_tonghuashun_derives_broken_from_touched_when_it_gives_no_open_count() -> None:
    raw = copy.deepcopy(ths_fixture())
    today = cast(
        dict[str, object],
        cast(dict[str, object], raw["limit_up_count"])["today"],
    )
    del today["open_num"]

    data = normalize_ths_limit_summary(raw, LimitSummaryRequest(DAY)).data

    # 44 touched the limit, 33 held it.
    assert (data["broken_count"], data["break_rate_percent"]) == (11, 25.0)


def test_tonghuashun_counts_its_pool_before_the_cut() -> None:
    raw = copy.deepcopy(ths_fixture())
    raw["page"] = {"limit": 200, "total": 250, "count": 2}
    cast(dict[str, object], cast(dict[str, object], raw["limit_up_count"])["today"])[
        "num"
    ] = 250

    result = normalize_ths_limit_pool(raw, LimitPoolRequest("limit_up", DAY, limit=5))

    assert (result.data["count"], len(items_of(result.data))) == (250, 5)


def ths_row(
    streak: str, packed: int | None = None, **changes: object
) -> dict[str, object]:
    rows = cast(list[dict[str, object]], ths_fixture()["info"])
    base = next(row for row in rows if row["code"] == "601579")
    return {**base, "high_days": streak, "high_days_value": packed, **changes}


@pytest.mark.parametrize(
    ("streak", "boards", "ceiling"),
    [
        ("首板", 1, None),
        ("3天3板", 3, None),
        # Two boards with a gap: only today's counts. Exact.
        ("4天2板", 1, None),
        # L L _ L or L _ L L: at least 1, at most 2.
        ("4天3板", 1, 2),
        ("5天4板", 1, 3),
        ("5天3板", 1, 2),
    ],
)
def test_a_broken_streak_counts_as_one_board_and_says_how_many_it_could_be(
    streak: str, boards: int, ceiling: int | None
) -> None:
    raw = {**ths_fixture(), "info": [ths_row(streak)]}

    pool = normalize_ths_limit_pool(raw, LimitPoolRequest("limit_up", DAY)).data
    summary = normalize_ths_limit_summary(raw, LimitSummaryRequest(DAY))

    assert items_of(pool)[0]["board_count"] == boards
    lower_bound = [w for w in summary.warnings if "梯队是下限" in w]
    if ceiling is None:
        assert lower_bound == []
    else:
        # The one row sits on the 1-board rung, so the height may be higher.
        assert lower_bound == [
            "有 1 只涨停股是「N天M板」且 M ≥ 3（中间断过板），同花顺分不出现在连了"
            f"几板，按 1 板计入梯队；梯队是下限。连板高度也可能不止 1 板，其中最多"
            f"可能到 {ceiling} 板。"
        ]


@pytest.mark.usefixtures("host_zone_is_not_beijing")
def test_the_tonghuashun_pool_reads_its_times_in_beijing_time() -> None:
    result = normalize_ths_limit_pool(
        ths_fixture(), LimitPoolRequest("limit_up", DAY, limit=200)
    )

    items = {item["code"]: item for item in items_of(result.data)}
    assert result.data["count"] == 33 and len(items) == 33
    # The host is forced to UTC: only the explicit Shanghai zone gives this.
    assert (items["601579"]["first_seal_time"], items["601579"]["streak"]) == (
        "09:30:22",
        "首板",
    )
    assert items["600825"]["first_seal_time"] == "09:25:01"
    # open_num is null, not 0, when the board never opened.
    assert items["001368"]["broken_times"] == 0
    assert items["601579"]["broken_times"] == 4
    # 4天3板 is not three in a row; East Money has it at lbc 1 as well.
    assert items["600418"]["board_count"] == 1
    assert codes(result.data)[-1] == "603396"
    assert items["603396"]["board_count"] is None
    assert items["600825"]["amount"] is None
    assert items["601579"] == {
        "code": "601579",
        "symbol": "601579.SH",
        "name": "会稽山",
        "price": 41.84,
        "change_percent": 9.99,
        "amount": None,
        "float_market_value": 20060749000,
        "turnover_rate": 11.24,
        "board_count": 1,
        "streak": "首板",
        "seal_fund": 19359368,
        "first_seal_time": "09:30:22",
        "last_seal_time": "15:00:00",
        "broken_times": 4,
        "limit_price": None,
        "yesterday_board_count": None,
        "industry": None,
    }
    assert (result.degraded, result.warnings) == (True, (THS_POOL_BASIS,))


def test_a_resealed_board_without_an_open_count_is_unknown_not_zero() -> None:
    """A null open_num means "never opened" only on a board that did not
    reseal; on one that did, the count is simply missing."""

    raw = {**ths_fixture(), "info": [ths_row("首板", 65537, open_num=None)]}

    data = normalize_ths_limit_pool(raw, LimitPoolRequest("limit_up", DAY)).data

    assert items_of(data)[0]["broken_times"] is None


def test_tonghuashun_has_no_stand_in_for_the_other_pools() -> None:
    with pytest.raises(UpstreamUnavailable) as caught:
        normalize_ths_limit_pool(ths_fixture(), LimitPoolRequest("broken", DAY))

    assert caught.value.retryable is False


@pytest.mark.parametrize(
    ("raw", "note"),
    [
        ({**ths_fixture(), "date": "20260924"}, "按空处理"),
        ({}, "可能不是交易日"),
        (
            {
                "info": [],
                "page": {"limit": 200, "total": 0, "count": 0, "page": 1},
                "limit_up_count": {"today": {"num": 0, "history_num": 0}},
                "limit_down_count": {"today": {"num": 0}},
                "date": "20260928",
            },
            "没有涨跌停股",
        ),
    ],
    ids=["another_day", "null_data", "no_rows"],
)
def test_an_empty_tonghuashun_day_is_an_empty_summary(raw: object, note: str) -> None:
    data = normalize_ths_limit_summary(raw, LimitSummaryRequest(DAY)).data

    assert data["items"] == [] and data["ladder"] == []
    assert (data["limit_up_count"], data["broken_count"]) == (0, 0)
    assert data["limit_down_count"] == 0
    assert note in cast(str, data["note"])


def test_tonghuashun_without_its_day_totals_is_unusable() -> None:
    raw = {
        key: value for key, value in ths_fixture().items() if key != "limit_up_count"
    }

    with pytest.raises(UpstreamUnavailable, match="汇总"):
        normalize_ths_limit_summary(raw, LimitSummaryRequest(DAY))
    with pytest.raises(UpstreamUnavailable):
        normalize_ths_limit_summary({"info": "?"}, LimitSummaryRequest(DAY))


def test_tonghuashun_says_when_it_sent_fewer_rows_than_it_counted() -> None:
    raw = {**ths_fixture(), "page": {"limit": 200, "total": 250, "count": 2}}

    result = normalize_ths_limit_summary(raw, LimitSummaryRequest(DAY))

    assert (
        "同花顺只返回了 33 / 250 只涨停股，明细和梯队只按这些计算；缺的是封板最早的"
        "那些（多为一字板和高位连板），连板高度和梯队可能偏低。"
    ) in result.warnings


@pytest.mark.parametrize(
    ("label", "boards"),
    [("首板", 1), ("3天3板", 3), ("4天3板", 1), (None, None)],
)
def test_tonghuashun_boards_come_from_the_label_without_the_packed_value(
    label: str | None, boards: int | None
) -> None:
    rows = cast(list[dict[str, object]], ths_fixture()["info"])
    row = {**rows[0], "high_days": label, "high_days_value": None}

    data = normalize_ths_limit_pool(
        {**ths_fixture(), "info": [row]}, LimitPoolRequest("limit_up", DAY)
    ).data

    assert items_of(data)[0]["board_count"] == boards
