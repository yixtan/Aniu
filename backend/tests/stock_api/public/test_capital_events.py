"""限售解禁 and 融资融券 for one stock, from East Money's datacenter.

The fixtures are 600487 亨通光电 as the gateway answered at about 01:46 on
2026-09-29, trimmed: five of its eleven 解禁 batches and the four newest of
its 两融 days; and 300750 宁德时代 as it answered at 02:58 the same night,
trimmed to three of fifteen batches and two days. Two readings go wrong
without these tests. SKILL.md takes
``FREE_SHARES`` — the circulating total after a lift — as the batch, which
overstates 600487's 2027 tranche about 380 times; and the 两融 report lags a
trading day, so its newest row on the morning of 09-29 was 09-24, not 09-28.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import httpx
import pytest

from backend.stock_api.public.contracts import LiftScheduleRequest, MarginDetailRequest
from backend.stock_api.public.errors import UpstreamUnavailable
from backend.stock_api.public.http import PublicHttpTransport
from backend.stock_api.public.normalizers.capital_events import (
    LIFT_BASIS,
    MARGIN_BASIS,
    NO_LIFT_RECORDS,
    NO_MARGIN_RECORDS,
    normalize_lift_schedule,
    normalize_margin_detail,
)
from backend.stock_api.public.providers.eastmoney import EastMoneyAdapter
from backend.stock_api.public.providers.eastmoney_datacenter import datacenter_rows
from backend.stock_api.public.providers.sina import SinaAdapter
from backend.stock_api.public.providers.tencent import TencentAdapter
from backend.stock_api.public.providers.ths import ThsAdapter
from backend.stock_api.public.router import PublicProviderAdapters, PublicStockRouter

_FIXTURES = Path(__file__).parent / "fixtures"

LIFT = LiftScheduleRequest("600487", as_of="2026-09-29")
MARGIN = MarginDetailRequest("600487", limit=10)

# The Shanghai exchange's own row for 600487 on 2026-09-24 (queryMargin.do,
# probed 2026-09-29 01:47), which East Money must match to the unit.
SSE_600487_20260924 = {
    "rzye": 7_766_902_324,
    "rzmre": 776_990_044,
    "rzche": 866_048_558,
    "rqyl": 529_400,
}


def fixture(name: str) -> object:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def rows_of(name: str) -> list[dict[str, object]]:
    return datacenter_rows(fixture(name), "fixture")


def items_of(data: dict[str, object], key: str = "items") -> list[dict[str, object]]:
    return cast(list[dict[str, object]], data[key])


def dates(batches: list[dict[str, object]]) -> list[object]:
    return [batch["date"] for batch in batches]


def transport_answering(
    payloads: dict[str, object],
) -> tuple[PublicHttpTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payloads[request.url.params["reportName"]])

    transport = PublicHttpTransport(transport=httpx.MockTransport(handler))
    transport._gates["eastmoney_f10"].minimum_start_interval = 0
    return transport, seen


def router_on(transport: PublicHttpTransport) -> PublicStockRouter:
    return PublicStockRouter(
        PublicProviderAdapters(
            eastmoney=EastMoneyAdapter(transport),
            tencent=TencentAdapter(transport),
            sina=SinaAdapter(transport),
            ths=ThsAdapter(transport),
        )
    )


def lift_row(
    day: str, shares_wan: float = 100.0, able_wan: float | None = None
) -> dict[str, object]:
    """A batch built from a real row. Every real row has ABLE_FREE_SHARES equal
    to CURRENT_FREE_SHARES, so ``able_wan`` is how a test tells them apart."""

    row = copy.deepcopy(rows_of("em_lift_600487.json")[2])
    row["FREE_DATE"] = f"{day} 00:00:00"
    row["CURRENT_FREE_SHARES"] = shares_wan
    row["ABLE_FREE_SHARES"] = shares_wan if able_wan is None else able_wan
    return row


# ---------------------------------------------------------------- requests


@pytest.mark.asyncio
async def test_lift_asks_for_every_batch_past_and_scheduled_in_one_call() -> None:
    transport, seen = transport_answering(
        {"RPT_LIFT_STAGE": fixture("em_lift_600487.json")}
    )

    rows = await EastMoneyAdapter(transport).lift_schedule(
        LIFT, timeout_seconds=2, cancellation_token=None
    )

    assert rows == rows_of("em_lift_600487.json")
    assert len(seen) == 1
    params = seen[0].url.params
    assert seen[0].url.host == "datacenter.eastmoney.com"
    assert params["filter"] == '(SECURITY_CODE="600487")'
    assert (params["sortColumns"], params["sortTypes"]) == ("FREE_DATE", "-1")
    assert (params["pageNumber"], params["pageSize"]) == ("1", "50")
    assert (params["source"], params["client"]) == ("WEB", "WEB")


@pytest.mark.asyncio
async def test_margin_asks_for_the_newest_days_filtered_on_scode() -> None:
    transport, seen = transport_answering(
        {"RPTA_WEB_RZRQ_GGMX": fixture("em_rzrq_600487.json")}
    )

    await EastMoneyAdapter(transport).margin_detail(
        MarginDetailRequest("600487.SH", limit=4),
        timeout_seconds=2,
        cancellation_token=None,
    )

    params = seen[0].url.params
    assert params["filter"] == '(SCODE="600487")'
    assert (params["sortColumns"], params["sortTypes"]) == ("DATE", "-1")
    assert params["pageSize"] == "4"


# ---------------------------------------------------------------- 解禁


def test_a_batch_is_its_own_size_in_shares_not_the_circulating_total() -> None:
    rows = rows_of("em_lift_600487.json")
    # What pinned the meaning down: the 2028 row's FREE_SHARES less its batch
    # is the 2027 row's FREE_SHARES. It is a running total, in 万股.
    assert cast(float, rows[0]["FREE_SHARES"]) - cast(
        float, rows[0]["CURRENT_FREE_SHARES"]
    ) == pytest.approx(cast(float, rows[1]["FREE_SHARES"]))

    result = normalize_lift_schedule(rows, LIFT)

    july = items_of(result.data)[0]
    assert july == {
        "date": "2026-07-08",
        "type": "股权激励限售股份",
        "shares": 8_465_132,
        "tradable_shares": 8_465_132,
        "total_ratio_percent": pytest.approx(0.3432),
        "float_ratio_percent": pytest.approx(0.345),
        "market_value": pytest.approx(651_476_558.72),
    }
    assert result.warnings == (LIFT_BASIS,)
    assert result.degraded is False


def test_nothing_in_the_next_90_days_still_names_the_next_batch() -> None:
    """On 2026-09-29 600487's next batch was 2027-06-21, well outside the
    window; an empty ``upcoming`` alone would read as "nothing ahead"."""

    result = normalize_lift_schedule(rows_of("em_lift_600487.json"), LIFT)

    assert result.data["upcoming"] == []
    assert dates(items_of(result.data)) == ["2026-07-08", "2022-06-16", "2021-06-16"]
    note = cast(str, result.data["note"])
    assert note.startswith("今天起 90 天内（至 2026-12-28）没有解禁")
    assert "2027-06-21" in note
    assert "6,466,060 股" in note
    assert "0.2622%" in note
    assert "共 2 批" in note


@pytest.mark.parametrize(
    ("as_of", "upcoming", "head_of_past"),
    [
        # Day 90 is inside the window.
        ("2027-03-23", ["2027-06-21"], "2026-07-08"),
        # Day 91 is not.
        ("2027-03-22", [], "2026-07-08"),
        # The day after, it has happened.
        ("2027-06-22", [], "2027-06-21"),
    ],
)
def test_the_window_runs_from_as_of_to_day_90(
    as_of: str, upcoming: list[str], head_of_past: str
) -> None:
    result = normalize_lift_schedule(
        rows_of("em_lift_600487.json"), LiftScheduleRequest("600487", as_of=as_of)
    )

    assert dates(items_of(result.data, "upcoming")) == upcoming
    assert items_of(result.data)[0]["date"] == head_of_past
    assert ("note" in result.data) is (not upcoming)


def test_a_batch_lifting_today_is_upcoming_and_named_in_the_note() -> None:
    """Its shares start trading today: supply a run before the open or in the
    session has to see, not history beside 「未来 90 天没有解禁」."""

    result = normalize_lift_schedule(
        rows_of("em_lift_600487.json"),
        LiftScheduleRequest("600487", as_of="2027-06-21"),
    )

    upcoming = items_of(result.data, "upcoming")
    assert dates(upcoming) == ["2027-06-21"]
    assert upcoming[0]["shares"] == 6_466_060
    assert items_of(result.data)[0]["date"] == "2026-07-08"
    assert result.data["note"] == "今天（2027-06-21）有一批解禁，是 upcoming 的第一条。"


def test_tradable_shares_come_from_their_own_column() -> None:
    """Holders under reduction rules can trade only part of a lifted batch."""

    result = normalize_lift_schedule(
        [lift_row("2026-11-10", shares_wan=1000.0, able_wan=250.0)], LIFT
    )

    batch = items_of(result.data, "upcoming")[0]
    assert (batch["shares"], batch["tradable_shares"]) == (10_000_000, 2_500_000)


def test_a_batch_without_a_tradable_figure_leaves_it_null() -> None:
    row = lift_row("2026-11-10", shares_wan=1000.0)
    row["ABLE_FREE_SHARES"] = None

    batch = items_of(normalize_lift_schedule([row], LIFT).data, "upcoming")[0]

    assert (batch["shares"], batch["tradable_shares"]) == (10_000_000, None)


def test_upcoming_is_soonest_first_and_the_past_is_capped_at_ten() -> None:
    # The gateway sorts FREE_DATE descending; the split re-sorts each side.
    scheduled = [lift_row("2026-12-01"), lift_row("2026-10-15")]
    history = [lift_row(f"20{year}-03-01") for year in range(25, 13, -1)]

    result = normalize_lift_schedule(scheduled + history, LIFT)

    assert dates(items_of(result.data, "upcoming")) == ["2026-10-15", "2026-12-01"]
    past = items_of(result.data)
    assert len(past) == 10
    assert dates(past)[:2] == ["2025-03-01", "2024-03-01"]
    assert "note" not in result.data


def test_a_stock_with_only_scheduled_batches_says_it_has_no_history() -> None:
    result = normalize_lift_schedule(
        rows_of("em_lift_600487.json"),
        LiftScheduleRequest("600487", as_of="2020-01-01"),
    )

    assert (result.data["upcoming"], result.data["items"]) == ([], [])
    note = cast(str, result.data["note"])
    assert "也没有已经解禁的批次" in note
    assert "2021-06-16" in note
    assert "共 5 批" in note


def test_no_lift_on_record_is_an_empty_success_not_an_outage() -> None:
    rows = datacenter_rows(fixture("datacenter_empty.json"), "RPT_LIFT_STAGE")

    result = normalize_lift_schedule(rows, LIFT)

    assert result.data == {
        "symbol": "600487.SH",
        "as_of": "2026-09-29",
        "upcoming": [],
        "items": [],
        "note": NO_LIFT_RECORDS,
    }
    assert result.warnings == ()


def test_a_shenzhen_stock_with_nothing_scheduled_says_so() -> None:
    """300750's last batch lifted on 2024-09-24; its 2021-06-11 首发 batch
    freed 41% of the company, the size a 解禁 check exists to catch."""

    result = normalize_lift_schedule(
        rows_of("em_lift_300750.json"),
        LiftScheduleRequest("300750.SZ", as_of="2026-09-29"),
    )

    assert result.data["symbol"] == "300750.SZ"
    assert result.data["upcoming"] == []
    past = items_of(result.data)
    assert dates(past) == ["2024-09-24", "2021-06-11", "2019-06-11"]
    ipo = past[1]
    assert ipo["type"] == "首发原股东限售股份"
    assert ipo["shares"] == 952_381_254
    assert ipo["total_ratio_percent"] == pytest.approx(40.884)
    assert result.data["note"] == (
        "今天起 90 天内（至 2026-12-28）没有解禁，之后也没有已排定的批次。"
    )


def test_a_batch_without_a_date_or_size_is_dropped_and_said_so() -> None:
    rows = rows_of("em_lift_600487.json")
    rows[3]["CURRENT_FREE_SHARES"] = None
    rows[4]["FREE_DATE"] = "--"

    result = normalize_lift_schedule(rows, LIFT)

    assert dates(items_of(result.data)) == ["2026-07-08"]
    assert result.degraded is True
    assert result.warnings[-1] == "限售解禁有 2 条记录因关键字段无效被丢弃。"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"data": []}, "不是列表"),
        ([{"SECURITY_CODE": "600487", "FREE_DATE": None}], "关键字段无效"),
        ([lift_row("2026-07-08") | {"SECURITY_CODE": "600519"}], "别的股票"),
        ([{"FREE_DATE": "2026-07-08 00:00:00", "CURRENT_FREE_SHARES": 1}], "别的股票"),
    ],
)
def test_a_lift_answer_that_cannot_be_read_is_an_upstream_failure(
    raw: object, message: str
) -> None:
    with pytest.raises(UpstreamUnavailable, match=message) as caught:
        normalize_lift_schedule(raw, LIFT)

    assert caught.value.error_category == "invalid_response"


# ---------------------------------------------------------------- 两融


def test_the_newest_margin_row_matches_the_shanghai_exchange_to_the_unit() -> None:
    result = normalize_margin_detail(rows_of("em_rzrq_600487.json"), MARGIN)

    newest = items_of(result.data)[0]
    assert newest == {
        "date": "2026-09-24",
        "financing_balance": SSE_600487_20260924["rzye"],
        "financing_buy": SSE_600487_20260924["rzmre"],
        "financing_repay": SSE_600487_20260924["rzche"],
        "financing_net_buy": -89_058_514,
        "short_shares": SSE_600487_20260924["rqyl"],
        "short_balance": 35_670_972,
        "total_balance": 7_802_573_296,
        "financing_balance_ratio": 4.6979,
        "close": 67.38,
    }
    # East Money's own net figure agrees with the one derived here.
    assert newest["financing_net_buy"] == rows_of("em_rzrq_600487.json")[0]["RZJME"]
    # 融券余额 is 余量 × close, so it moves with the price alone; Shanghai's
    # per-stock detail gives no amount of its own (rqylje null).
    assert newest["short_balance"] == round(529_400 * 67.38)


def test_margin_says_its_newest_row_is_a_trading_day_behind() -> None:
    """At 01:46 on 2026-09-29 the newest row was 09-24; 09-28 was not out."""

    result = normalize_margin_detail(rows_of("em_rzrq_600487.json"), MARGIN)

    # The dated note carries the T+1 caveat; the warnings do not repeat it.
    assert result.warnings == (MARGIN_BASIS,)
    assert result.data["note"] == (
        "最新一行是 2026-09-24 收盘后的余额；"
        "两融 T+1 公布，这一行之后的交易日还没有数据。"
    )
    # Said for every exchange: Shenzhen publishes a 融券余额, Shanghai does not.
    assert "交易所不公布" not in MARGIN_BASIS
    assert result.data["symbol"] == "600487.SH"


def test_margin_is_newest_first_whatever_order_the_page_came_in() -> None:
    rows = rows_of("em_rzrq_600487.json")[::-1]

    result = normalize_margin_detail(rows, MarginDetailRequest("600487", limit=2))

    assert dates(items_of(result.data)) == ["2026-09-24", "2026-09-23"]


def test_net_buying_falls_back_to_east_moneys_figure_when_a_leg_is_missing() -> None:
    rows = rows_of("em_rzrq_600487.json")[:1]
    rows[0]["RZCHE"] = None

    result = normalize_margin_detail(rows, MARGIN)

    assert items_of(result.data)[0]["financing_net_buy"] == -89_058_514


def test_a_shenzhen_margin_row_reads_the_same_way() -> None:
    result = normalize_margin_detail(
        rows_of("em_rzrq_300750.json"), MarginDetailRequest("300750", limit=10)
    )

    newest = items_of(result.data)[0]
    assert result.data["symbol"] == "300750.SZ"
    assert dates(items_of(result.data)) == ["2026-09-24", "2026-09-23"]
    assert newest["financing_balance"] == 24_371_160_223
    assert newest["financing_net_buy"] == 596_114_538 - 879_492_815
    assert newest["short_balance"] == round(1_246_598 * 293.5)
    assert newest["financing_balance_ratio"] == 1.9489


def test_a_stock_that_is_not_a_margin_target_is_an_empty_success() -> None:
    rows = datacenter_rows(fixture("datacenter_empty.json"), "RPTA_WEB_RZRQ_GGMX")

    result = normalize_margin_detail(rows, MARGIN)

    assert result.data == {
        "symbol": "600487.SH",
        "items": [],
        "note": NO_MARGIN_RECORDS,
    }
    assert result.warnings == ()


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (None, "不是列表"),
        ([{"SCODE": "600487", "DATE": "2026-09-24 00:00:00"}], "关键字段无效"),
        ([{"SCODE": "000001", "DATE": "2026-09-24", "RZYE": 1}], "别的股票"),
    ],
)
def test_a_margin_answer_that_cannot_be_read_is_an_upstream_failure(
    raw: object, message: str
) -> None:
    with pytest.raises(UpstreamUnavailable, match=message):
        normalize_margin_detail(raw, MARGIN)


# ---------------------------------------------------------------- router


@pytest.mark.asyncio
async def test_the_router_serves_both_from_east_money() -> None:
    transport, seen = transport_answering(
        {
            "RPT_LIFT_STAGE": fixture("em_lift_600487.json"),
            "RPTA_WEB_RZRQ_GGMX": fixture("em_rzrq_600487.json"),
        }
    )
    router = router_on(transport)

    lift = await router.execute(LIFT)
    margin = await router.execute(MarginDetailRequest("600487", limit=4))

    for result in (lift, margin):
        meta = cast(dict[str, object], result["meta"])
        assert meta["source"] == "eastmoney"
        assert meta["fallback_used"] is False
    assert len(items_of(cast(dict[str, object], lift["data"]))) == 3
    assert len(items_of(cast(dict[str, object], margin["data"]))) == 4
    assert [request.url.params["reportName"] for request in seen] == [
        "RPT_LIFT_STAGE",
        "RPTA_WEB_RZRQ_GGMX",
    ]


@pytest.mark.asyncio
async def test_through_the_router_no_records_succeeds_and_a_refusal_fails() -> None:
    refusal = {
        "version": None,
        "result": None,
        "success": False,
        "message": "请求参数错误",
        "code": 9501,
    }
    transport, _ = transport_answering(
        {
            "RPT_LIFT_STAGE": fixture("datacenter_empty.json"),
            "RPTA_WEB_RZRQ_GGMX": refusal,
        }
    )
    router = router_on(transport)

    lift = await router.execute(LIFT)
    with pytest.raises(UpstreamUnavailable, match="请求参数错误"):
        await router.execute(MARGIN)

    assert cast(dict[str, object], lift["data"])["note"] == NO_LIFT_RECORDS
