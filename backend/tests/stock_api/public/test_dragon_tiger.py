"""龙虎榜, read from East Money's datacenter the way the model needs it.

The fixtures are the gateway's own answers taken at 03:00 on 2026-09-29:
600487's listings (2026-08-25 and 08-05) and both seat tables for 08-25, and
the whole list for 2026-09-28 cut from 67 rows to 14 that keep the awkward
cases — 002614 listed twice with the same seats, 002342 for a one-day and a
three-day reason, 601218 and 920025 three times, a 北交所 code, a 风险警示板
and a 科创板 row. The empty answer is the one datacenter_empty.json holds;
601398 got it byte for byte in the same run.
"""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest

import backend.stock_api.public.cache as cache_module
import backend.stock_api.public.router as router_module
from backend.stock_api.public.cache import PublicStockDataCache
from backend.stock_api.public.contracts import (
    DragonTigerMarketRequest,
    DragonTigerStockRequest,
)
from backend.stock_api.public.errors import UpstreamUnavailable
from backend.stock_api.public.http import PublicHttpTransport
from backend.stock_api.public.normalizers.dragon_tiger import (
    MARKET_BASIS,
    MARKET_EMPTY_NOTE,
    STOCK_BASIS,
    normalize_dragon_tiger_market,
    normalize_dragon_tiger_stock,
)
from backend.stock_api.public.providers import eastmoney_dragon_tiger
from backend.stock_api.public.providers.eastmoney import EastMoneyAdapter
from backend.stock_api.public.providers.sina import SinaAdapter
from backend.stock_api.public.providers.tencent import TencentAdapter
from backend.stock_api.public.providers.ths import ThsAdapter
from backend.stock_api.public.router import PublicProviderAdapters, PublicStockRouter
from backend.stock_api.public.service import (
    PARTIAL_RESULT_TTL_SECONDS,
    StockMarketDataService,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_REPORT_FIXTURES = {
    "RPT_DAILYBILLBOARD_DETAILSNEW": "em_lhb_stock_600487.json",
    "RPT_BILLBOARD_DAILYDETAILSBUY": "em_lhb_buy_seats_600487_20260825.json",
    "RPT_BILLBOARD_DAILYDETAILSSELL": "em_lhb_sell_seats_600487_20260825.json",
}
_EMPTY = "datacenter_empty.json"

Json = dict[str, object]


def fixture(name: str) -> Json:
    return cast(Json, json.loads((_FIXTURES / name).read_text(encoding="utf-8")))


def rows_of(name: str) -> list[Json]:
    result = cast(Json, fixture(name)["result"])
    return cast(list[Json], result["data"])


def stock_raw(**changes: object) -> Json:
    """The adapter's answer for 600487 with its 2026-08-25 seats."""

    raw: Json = {
        "since": "2026-07-01",
        "records": rows_of("em_lhb_stock_600487.json"),
        "seat_date": "2026-08-25",
        "buy": rows_of("em_lhb_buy_seats_600487_20260825.json"),
        "sell": rows_of("em_lhb_sell_seats_600487_20260825.json"),
        "seat_error": None,
    }
    raw.update(changes)
    return raw


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        eastmoney_dragon_tiger, "_shanghai_today", lambda: date(2026, 9, 29)
    )


def transport_answering(
    answer: dict[str, str] | None = None,
    *,
    respond: object = None,
) -> tuple[PublicHttpTransport, list[httpx.Request]]:
    """A transport that answers each report with its fixture, or ``respond``
    (a callable taking the request) when given."""

    seen: list[httpx.Request] = []
    by_report = answer if answer is not None else _REPORT_FIXTURES

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if callable(respond):
            return httpx.Response(200, json=respond(request))
        return httpx.Response(
            200, json=fixture(by_report[request.url.params["reportName"]])
        )

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


def listing(data: Json, index: int = 0) -> Json:
    seats = cast(Json, data["seats"])
    return cast(list[Json], seats["listings"])[index]


def market_raw(rows: list[Json], expected: int | None = None) -> Json:
    """`dragon_tiger_market`'s answer: the day's rows and the gateway's count."""

    return {"rows": rows, "expected_count": expected}


# Adapter --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_listed_stock_costs_three_requests_listings_first() -> None:
    transport, seen = transport_answering()

    result = await router_on(transport).execute(DragonTigerStockRequest("600487"))

    reports = [request.url.params["reportName"] for request in seen]
    assert reports == [
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        "RPT_BILLBOARD_DAILYDETAILSBUY",
        "RPT_BILLBOARD_DAILYDETAILSSELL",
    ]
    listings, buy, sell = (request.url.params for request in seen)
    # 90 days back from 2026-09-29, newest first, one page of `limit` rows.
    assert listings["filter"] == (
        "(SECURITY_CODE=\"600487\")(TRADE_DATE>='2026-07-01')"
    )
    assert (listings["sortColumns"], listings["sortTypes"]) == ("TRADE_DATE", "-1")
    assert listings["pageSize"] == "5"
    # Seats for the newest listing, 50 a page so three reasons' seats fit.
    assert buy["filter"] == "(TRADE_DATE='2026-08-25')(SECURITY_CODE=\"600487\")"
    assert (buy["sortColumns"], buy["sortTypes"], buy["pageSize"]) == (
        "BUY",
        "-1",
        "50",
    )
    assert (sell["filter"], sell["sortColumns"]) == (buy["filter"], "SELL")
    assert all(request.url.host == "datacenter.eastmoney.com" for request in seen)
    meta = cast(Json, result["meta"])
    assert (meta["source"], meta["degraded"]) == ("eastmoney", False)
    data = cast(Json, result["data"])
    assert cast(Json, data["seats"])["date"] == "2026-08-25"


@pytest.mark.asyncio
async def test_a_stock_not_listed_costs_one_request_and_succeeds() -> None:
    transport, seen = transport_answering({"RPT_DAILYBILLBOARD_DETAILSNEW": _EMPTY})

    result = await router_on(transport).execute(DragonTigerStockRequest("601398"))

    assert len(seen) == 1
    assert result["data"] == {
        "symbol": "601398.SH",
        "since": "2026-07-01",
        "items": [],
        "seats": None,
        "note": "2026-07-01 以来没有上过龙虎榜。",
    }


@pytest.mark.asyncio
async def test_an_older_date_widens_the_window_and_chooses_the_seats() -> None:
    older = {**rows_of("em_lhb_stock_600487.json")[0], "TRADE_DATE": "2026-05-06"}

    def respond(request: httpx.Request) -> object:
        answer = fixture(_REPORT_FIXTURES[request.url.params["reportName"]])
        if request.url.params["reportName"] == "RPT_DAILYBILLBOARD_DETAILSNEW":
            result = cast(Json, answer["result"])
            result["data"] = [*cast(list[Json], result["data"]), older]
        return answer

    transport, seen = transport_answering(respond=respond)

    await EastMoneyAdapter(transport).dragon_tiger_stock(
        DragonTigerStockRequest("600487", limit=20, trade_date="2026-05-06"),
        timeout_seconds=1.0,
        cancellation_token=None,
    )

    listings, buy, _ = (request.url.params for request in seen)
    assert "(TRADE_DATE>='2026-05-06')" in listings["filter"]
    assert listings["pageSize"] == "20"
    assert buy["filter"].startswith("(TRADE_DATE='2026-05-06')")


@pytest.mark.asyncio
async def test_a_date_missing_from_a_short_page_asks_for_no_seats() -> None:
    # 600487 for 2026-09-28 in the e2e run at 03:14 on 09-29: the page held
    # both of its listings since 07-01, neither on 09-28, and the two seat
    # requests that followed came back empty.
    transport, seen = transport_answering()

    result = await router_on(transport).execute(
        DragonTigerStockRequest("600487", trade_date="2026-09-28")
    )

    assert len(seen) == 1
    data = cast(Json, result["data"])
    assert data["seats"] is None
    assert data["note"] == "2026-09-28 这只股票没有上龙虎榜；最近的上榜见 items。"
    assert len(cast(list[Json], data["items"])) == 2


@pytest.mark.asyncio
async def test_a_date_missing_from_a_full_page_still_asks_for_its_seats() -> None:
    # Two listings on a page of two: an older one can sit past the page.
    transport, seen = transport_answering()

    await EastMoneyAdapter(transport).dragon_tiger_stock(
        DragonTigerStockRequest("600487", limit=2, trade_date="2026-07-15"),
        timeout_seconds=1.0,
        cancellation_token=None,
    )

    assert [request.url.params["reportName"] for request in seen] == [
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        "RPT_BILLBOARD_DAILYDETAILSBUY",
        "RPT_BILLBOARD_DAILYDETAILSSELL",
    ]


@pytest.mark.asyncio
async def test_a_failed_seat_request_keeps_the_listings_and_says_so() -> None:
    refused = {"success": False, "code": 9501, "message": "请求参数错误"}

    def respond(request: httpx.Request) -> object:
        if request.url.params["reportName"] == "RPT_BILLBOARD_DAILYDETAILSBUY":
            return refused
        return fixture(_REPORT_FIXTURES[request.url.params["reportName"]])

    transport, seen = transport_answering(respond=respond)

    result = await router_on(transport).execute(DragonTigerStockRequest("600487"))

    # The sell side is not asked for once the buy side failed: half the seats
    # would read as "nobody sold". The refusal came inside an HTTP 200, so
    # the transport did not send it twice either.
    assert [request.url.params["reportName"] for request in seen] == [
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        "RPT_BILLBOARD_DAILYDETAILSBUY",
    ]
    data = cast(Json, result["data"])
    meta = cast(Json, result["meta"])
    assert len(cast(list[Json], data["items"])) == 2
    assert data["seats"] is None
    assert meta["degraded"] is True
    assert any(
        "席位明细这次没取到" in warning and "9501" in warning
        for warning in cast(list[str], meta["warnings"])
    )


@pytest.mark.asyncio
async def test_the_market_is_one_page_sorted_by_code_for_stable_paging() -> None:
    transport, seen = transport_answering(
        {"RPT_DAILYBILLBOARD_DETAILSNEW": "em_lhb_market_20260928.json"}
    )

    result = await router_on(transport).execute(
        DragonTigerMarketRequest("2026-09-28", limit=3)
    )

    assert len(seen) == 1
    params = seen[0].url.params
    assert params["filter"] == "(TRADE_DATE>='2026-09-28')(TRADE_DATE<='2026-09-28')"
    assert (params["sortColumns"], params["sortTypes"]) == (
        "SECURITY_CODE,TRADE_DATE",
        "1,-1",
    )
    assert params["pageSize"] == "500"
    data = cast(Json, result["data"])
    assert (data["row_count"], data["stock_count"]) == (14, 8)
    assert len(cast(list[Json], data["items"])) == 3


@pytest.mark.asyncio
async def test_a_full_market_page_asks_for_the_next_and_drops_repeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eastmoney_dragon_tiger, "MARKET_PAGE_SIZE", 5)
    rows = rows_of("em_lhb_market_20260928.json")
    # Pages that overlap by one row, as a sort with ties can.
    pages = {"1": rows[0:5], "2": rows[4:9], "3": rows[9:12]}

    def respond(request: httpx.Request) -> object:
        page = pages[request.url.params["pageNumber"]]
        return {"success": True, "code": 0, "result": {"data": page}}

    transport, seen = transport_answering(respond=respond)

    raw = cast(
        Json,
        await EastMoneyAdapter(transport).dragon_tiger_market(
            DragonTigerMarketRequest("2026-09-28"),
            timeout_seconds=1.0,
            cancellation_token=None,
        ),
    )

    assert [request.url.params["pageNumber"] for request in seen] == ["1", "2", "3"]
    trade_ids = [row["TRADE_ID"] for row in cast(list[Json], raw["rows"])]
    assert trade_ids == [row["TRADE_ID"] for row in rows[:12]]
    # No count in these answers: the short third page is what ended it.
    assert raw["expected_count"] is None


@pytest.mark.asyncio
async def test_a_page_cut_short_by_the_gateway_does_not_end_the_day() -> None:
    """Another exchange endpoint served 2000 rows to a pageSize of 5000. Here
    every page is capped at 5 while ``count`` says 14: the loop follows the
    count, not the length of a page."""

    rows = rows_of("em_lhb_market_20260928.json")

    def respond(request: httpx.Request) -> object:
        start = (int(request.url.params["pageNumber"]) - 1) * 5
        return {
            "success": True,
            "code": 0,
            "result": {"data": rows[start : start + 5], "count": 14, "pages": 1},
        }

    transport, seen = transport_answering(respond=respond)

    result = await router_on(transport).execute(
        DragonTigerMarketRequest("2026-09-28", limit=200)
    )

    assert [request.url.params["pageNumber"] for request in seen] == ["1", "2", "3"]
    data = cast(Json, result["data"])
    assert (data["row_count"], data["stock_count"]) == (14, 8)
    meta = cast(Json, result["meta"])
    assert meta["degraded"] is False
    assert meta["warnings"] == [MARKET_BASIS]


@pytest.mark.asyncio
async def test_rows_short_of_the_count_are_said_to_be_short() -> None:
    """A gateway that places page 2 at row 500 of a 5-row page skips rows and
    answers page 2 empty; the counts are then lower bounds, and say so."""

    rows = rows_of("em_lhb_market_20260928.json")

    def respond(request: httpx.Request) -> object:
        page = rows[:5] if request.url.params["pageNumber"] == "1" else []
        return {"success": True, "code": 0, "result": {"data": page, "count": 14}}

    transport, seen = transport_answering(respond=respond)

    result = await router_on(transport).execute(DragonTigerMarketRequest("2026-09-28"))

    assert len(seen) == 2
    meta = cast(Json, result["meta"])
    assert meta["degraded"] is True
    assert meta["warnings"] == [
        MARKET_BASIS,
        "东方财富只返回了当天 14 行中的 5 行，row_count 和 stock_count 偏低；"
        "缺的是代码靠后的股票（沪市、科创板、北交所）。",
    ]


@pytest.mark.asyncio
async def test_an_empty_market_day_is_a_success_with_zero_counts() -> None:
    transport, _ = transport_answering({"RPT_DAILYBILLBOARD_DETAILSNEW": _EMPTY})

    result = await router_on(transport).execute(DragonTigerMarketRequest("2026-09-29"))

    # Zero counts and no items: what market_sentiment reads as "not published
    # yet" before it asks the trading day before.
    assert result["data"] == {
        "trade_date": "2026-09-29",
        "row_count": 0,
        "stock_count": 0,
        "items": [],
        "note": MARKET_EMPTY_NOTE,
    }
    # Nothing for the basis to explain.
    assert cast(Json, result["meta"])["warnings"] == []


@pytest.mark.asyncio
async def test_seats_that_run_into_the_deadline_leave_the_listings_standing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The router cancels a call at its deadline, listings and all. A hanging
    seat request gives up just before, so the listings come back."""

    monkeypatch.setitem(router_module._TOTAL_TIMEOUTS, "signals", 0.6)
    monkeypatch.setattr(eastmoney_dragon_tiger, "SEAT_MARGIN_SECONDS", 0.1)
    monkeypatch.setattr(eastmoney_dragon_tiger, "SEAT_MINIMUM_SECONDS", 0.1)
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        report = request.url.params["reportName"]
        seen.append(report)
        if report == "RPT_BILLBOARD_DAILYDETAILSSELL":
            await asyncio.sleep(10)
        return httpx.Response(200, json=fixture(_REPORT_FIXTURES[report]))

    transport = PublicHttpTransport(transport=httpx.MockTransport(handler))
    transport._gates["eastmoney_f10"].minimum_start_interval = 0

    result = await router_on(transport).execute(DragonTigerStockRequest("600487"))

    assert seen[:3] == [
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        "RPT_BILLBOARD_DAILYDETAILSBUY",
        "RPT_BILLBOARD_DAILYDETAILSSELL",
    ]
    data = cast(Json, result["data"])
    meta = cast(Json, result["meta"])
    assert len(cast(list[Json], data["items"])) == 2
    assert data["seats"] is None
    assert (meta["degraded"], meta["partial"]) == (True, True)
    assert "席位明细这次没取到（席位查询超过总超时。），只返回了上榜记录。" in cast(
        list[str], meta["warnings"]
    )


@pytest.mark.asyncio
async def test_listings_whose_seats_failed_are_asked_again_soon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One refused seat request must not hide the seats for the 10 minutes a
    龙虎榜 answer is otherwise kept."""

    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(
        cache_module, "time", SimpleNamespace(monotonic=lambda: clock.now)
    )
    refuse_sell = True

    def respond(request: httpx.Request) -> object:
        report = request.url.params["reportName"]
        if report == "RPT_BILLBOARD_DAILYDETAILSSELL" and refuse_sell:
            return {"success": False, "code": 9501, "message": "请求参数错误"}
        return fixture(_REPORT_FIXTURES[report])

    transport, seen = transport_answering(respond=respond)
    service = StockMarketDataService(router_on(transport), cache=PublicStockDataCache())
    request = DragonTigerStockRequest("600487")

    first = await service.execute(request)
    refuse_sell = False
    clock.now += PARTIAL_RESULT_TTL_SECONDS - 1
    cached = await service.execute(request)
    clock.now += 2
    second = await service.execute(request)
    clock.now += 60
    kept = await service.execute(request)

    assert cast(Json, first["meta"])["partial"] is True
    assert cached == first
    assert cast(Json, second["data"])["seats"] is not None
    assert "partial" not in cast(Json, second["meta"])
    assert kept == second
    # 3 for the first call, 3 for the second, none from the cache.
    assert len(seen) == 6


# One stock ------------------------------------------------------------------


def test_listings_come_newest_first_in_yuan_and_percent() -> None:
    raw = stock_raw(records=rows_of("em_lhb_stock_600487.json")[::-1])

    data = normalize_dragon_tiger_stock(raw, DragonTigerStockRequest("600487")).data

    items = cast(list[Json], data["items"])
    assert [item["date"] for item in items] == ["2026-08-25", "2026-08-05"]
    assert items[0] == {
        "date": "2026-08-25",
        "reason": "有价格涨跌幅限制的日价格振幅达到15%的前五只证券",
        "trade_id": "100401673",
        "close": 64.6,
        "change_percent": 6.9005,
        "turnover_rate": 10.6023,
        "buy_amount": 2663983257.26,
        "sell_amount": 814390041.46,
        "net_amount": 1849593215.8,
        "commentary": "1家机构买入，成功率42.50%",
    }
    assert (data["symbol"], data["since"], data["note"]) == (
        "600487.SH",
        "2026-07-01",
        None,
    )


def test_limit_cuts_the_listings_but_not_the_seats() -> None:
    data = normalize_dragon_tiger_stock(
        stock_raw(), DragonTigerStockRequest("600487", limit=1)
    ).data

    assert [item["date"] for item in cast(list[Json], data["items"])] == ["2026-08-25"]
    assert cast(Json, data["seats"])["date"] == "2026-08-25"


def test_a_seat_on_both_lists_shows_both_sides_and_others_are_undisclosed() -> None:
    normalized = normalize_dragon_tiger_stock(
        stock_raw(), DragonTigerStockRequest("600487")
    )

    first = listing(normalized.data)
    assert (first["trade_id"], first["reason"]) == (
        "100401673",
        "有价格涨跌幅限制的日价格振幅达到15%的前五只证券",
    )
    buy = cast(list[Json], first["buy"])
    sell = cast(list[Json], first["sell"])
    # 沪股通专用 bought 10.89 亿 and sold 3.35 亿; each table carries one side.
    connect = {
        "name": "沪股通专用",
        "buy_amount": 1088981452.97,
        "sell_amount": 334786612.54,
        "kind": "connect",
    }
    assert buy[0] == connect
    assert sell[0] == connect
    # 机构专用 shares code 0 with every institution, so its sell side is not
    # known, not zero.
    assert buy[1] == {
        "name": "机构专用",
        "buy_amount": 656306893.06,
        "sell_amount": None,
        "kind": "institution",
    }
    cicc = "中国国际金融股份有限公司上海分公司"
    assert [(s["buy_amount"], s["sell_amount"]) for s in buy if s["name"] == cicc] == [
        (420992331.92, 116017072.05)
    ]
    assert sell[1]["buy_amount"] is None
    assert [s["kind"] for s in buy] == [
        "connect",
        "institution",
        "other",
        "other",
        "other",
    ]
    assert [s["sell_amount"] for s in sell] == sorted(
        (cast(float, s["sell_amount"]) for s in sell), reverse=True
    )


def test_institutions_on_both_sides_never_merge() -> None:
    """Every 机构专用 seat has code 0, so a buying institution and a selling one
    are two institutions, not one that did both. Built from the real 08-25
    tables with an institution added to the sell side."""

    institution = copy.deepcopy(rows_of("em_lhb_sell_seats_600487_20260825.json")[0])
    institution.update(
        {"OPERATEDEPT_CODE": "0", "OPERATEDEPT_NAME": "机构专用", "SELL": 99_000_000.0}
    )
    sell = [*rows_of("em_lhb_sell_seats_600487_20260825.json"), institution]

    normalized = normalize_dragon_tiger_stock(
        stock_raw(sell=sell), DragonTigerStockRequest("600487")
    )

    first = listing(normalized.data)
    buying = [s for s in cast(list[Json], first["buy"]) if s["kind"] == "institution"]
    selling = [s for s in cast(list[Json], first["sell"]) if s["kind"] == "institution"]
    assert buying == [
        {
            "name": "机构专用",
            "buy_amount": 656306893.06,
            "sell_amount": None,
            "kind": "institution",
        }
    ]
    assert selling == [
        {
            "name": "机构专用",
            "buy_amount": None,
            "sell_amount": 99000000.0,
            "kind": "institution",
        }
    ]
    seats = cast(Json, normalized.data["seats"])
    assert seats["institution_net"] == pytest.approx(656306893.06 - 99_000_000)
    assert first["institution_net"] == pytest.approx(656306893.06 - 99_000_000)


def test_a_shenzhen_connect_seat_counts_as_connect_and_merges_across_sides() -> None:
    """Every 000/002/300 stock's connect seat is 深股通专用. No Shenzhen seat
    table was probed, so this renames 600487's 沪股通 rows onto a Shenzhen
    code; the classification reads the name, the merge the seat code."""

    def to_shenzhen(rows: list[Json]) -> list[Json]:
        moved = copy.deepcopy(rows)
        for row in moved:
            row["SECURITY_CODE"] = "000823"
            if row.get("OPERATEDEPT_NAME") == "沪股通专用":
                row["OPERATEDEPT_NAME"] = "深股通专用"
                row["OPERATEDEPT_CODE"] = "10434471"
        return moved

    raw = stock_raw(
        records=to_shenzhen(rows_of("em_lhb_stock_600487.json")),
        buy=to_shenzhen(rows_of("em_lhb_buy_seats_600487_20260825.json")),
        sell=to_shenzhen(rows_of("em_lhb_sell_seats_600487_20260825.json")),
    )

    data = normalize_dragon_tiger_stock(raw, DragonTigerStockRequest("000823")).data

    first = listing(data)
    connect = {
        "name": "深股通专用",
        "buy_amount": 1088981452.97,
        "sell_amount": 334786612.54,
        "kind": "connect",
    }
    assert cast(list[Json], first["buy"])[0] == connect
    assert cast(list[Json], first["sell"])[0] == connect
    seats = cast(Json, data["seats"])
    assert seats["connect_net"] == 754194840.43
    assert first["connect_net"] == 754194840.43


def test_institution_and_connect_nets_match_the_hand_count() -> None:
    normalized = normalize_dragon_tiger_stock(
        stock_raw(), DragonTigerStockRequest("600487")
    )

    # Checked by hand in the research: institutions +6.56 亿 (one seat, buy
    # only), 沪股通 10.89 − 3.35 = +7.54 亿.
    seats = cast(Json, normalized.data["seats"])
    assert (seats["institution_net"], seats["connect_net"]) == (
        656306893.06,
        754194840.43,
    )
    first = listing(normalized.data)
    assert (first["institution_net"], first["connect_net"]) == (
        656306893.06,
        754194840.43,
    )
    assert normalized.warnings == (STOCK_BASIS,)
    assert normalized.degraded is False


def test_no_seat_of_a_kind_is_null_not_zero() -> None:
    buy = [
        row
        for row in rows_of("em_lhb_buy_seats_600487_20260825.json")
        if row["OPERATEDEPT_CODE"] not in {"0", "10434470"}
    ]
    sell = [
        row
        for row in rows_of("em_lhb_sell_seats_600487_20260825.json")
        if row["OPERATEDEPT_CODE"] != "10434470"
    ]

    data = normalize_dragon_tiger_stock(
        stock_raw(buy=buy, sell=sell), DragonTigerStockRequest("600487")
    ).data

    seats = cast(Json, data["seats"])
    assert (seats["institution_net"], seats["connect_net"]) == (None, None)


def test_two_reasons_with_the_same_seats_are_counted_once_for_the_day() -> None:
    # Constructed from the real 08-25 seats: 002614 and 601218 were listed
    # twice on 09-28 under two one-day reasons with identical nets, which is
    # what a second TRADE_ID carrying the same rows looks like.
    raw = stock_raw()
    for side in ("buy", "sell"):
        copies = copy.deepcopy(cast(list[Json], raw[side]))
        for row in copies:
            row["TRADE_ID"] = "100401674"
            row["EXPLANATION"] = "有价格涨跌幅限制的日换手率达到20%的前五只证券"
        raw[side] = [*cast(list[Json], raw[side]), *copies]

    normalized = normalize_dragon_tiger_stock(raw, DragonTigerStockRequest("600487"))

    seats = cast(Json, normalized.data["seats"])
    listings = cast(list[Json], seats["listings"])
    assert [(item["trade_id"], item["reason"]) for item in listings] == [
        ("100401673", "有价格涨跌幅限制的日价格振幅达到15%的前五只证券"),
        ("100401674", "有价格涨跌幅限制的日换手率达到20%的前五只证券"),
    ]
    assert (seats["institution_net"], seats["connect_net"]) == (
        656306893.06,
        754194840.43,
    )
    assert normalized.warnings == (STOCK_BASIS,)


def test_reasons_with_different_windows_are_added_and_flagged() -> None:
    # Constructed: 002342 on 09-28 had a one-day reason and a three-day one
    # whose seats cover all three days, so its amounts differ.
    raw = stock_raw()
    for side, column in (("buy", "BUY"), ("sell", "SELL")):
        copies = copy.deepcopy(cast(list[Json], raw[side]))
        for row in copies:
            row["TRADE_ID"] = "100401675"
            row[column] = cast(float, row[column]) * 2
        raw[side] = [*cast(list[Json], raw[side]), *copies]

    normalized = normalize_dragon_tiger_stock(raw, DragonTigerStockRequest("600487"))

    seats = cast(Json, normalized.data["seats"])
    assert seats["institution_net"] == pytest.approx(656306893.06 * 3)
    assert listing(normalized.data, 1)["institution_net"] == pytest.approx(
        656306893.06 * 2
    )
    assert any("统计窗口不同" in warning for warning in normalized.warnings)


def test_a_date_the_stock_was_not_listed_has_no_seats_and_a_note() -> None:
    data = normalize_dragon_tiger_stock(
        stock_raw(seat_date="2026-08-06", buy=[], sell=[]),
        DragonTigerStockRequest("600487", trade_date="2026-08-06"),
    ).data

    assert data["seats"] is None
    assert data["note"] == "2026-08-06 这只股票没有上龙虎榜；最近的上榜见 items。"
    assert len(cast(list[Json], data["items"])) == 2


def test_no_listing_carries_no_seat_basis() -> None:
    normalized = normalize_dragon_tiger_stock(
        {
            "since": "2026-07-01",
            "records": [],
            "seat_date": None,
            "buy": [],
            "sell": [],
        },
        DragonTigerStockRequest("601398"),
    )

    assert normalized.data["note"] == "2026-07-01 以来没有上过龙虎榜。"
    assert normalized.warnings == ()
    assert normalized.partial is False


def test_a_listing_whose_seats_are_missing_says_so() -> None:
    data = normalize_dragon_tiger_stock(
        stock_raw(buy=[], sell=[]), DragonTigerStockRequest("600487")
    ).data

    assert data["seats"] is None
    assert data["note"] == "2026-08-25 的席位明细还没有公布或为空。"


def test_rows_for_another_stock_or_date_are_left_out() -> None:
    other = copy.deepcopy(rows_of("em_lhb_stock_600487.json")[0])
    other["SECURITY_CODE"] = "600519"
    stray_seat = copy.deepcopy(rows_of("em_lhb_buy_seats_600487_20260825.json")[0])
    stray_seat["TRADE_DATE"] = "2026-08-05 00:00:00"
    raw = stock_raw(
        records=[other, *rows_of("em_lhb_stock_600487.json")],
        buy=[stray_seat, *rows_of("em_lhb_buy_seats_600487_20260825.json")],
    )

    data = normalize_dragon_tiger_stock(raw, DragonTigerStockRequest("600487")).data

    assert len(cast(list[Json], data["items"])) == 2
    assert len(cast(list[Json], listing(data)["buy"])) == 5


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {"records": None},
        {"records": [{"SECURITY_CODE": "600487", "TRADE_DATE": "不是日期"}]},
        {**stock_raw(), "buy": {"not": "a list"}},
    ],
)
def test_an_unusable_stock_answer_is_a_retryable_outage(raw: object) -> None:
    with pytest.raises(UpstreamUnavailable) as caught:
        normalize_dragon_tiger_stock(raw, DragonTigerStockRequest("600487"))

    assert caught.value.retryable is True


# The whole market -----------------------------------------------------------


def test_the_market_is_counted_before_it_is_cut_and_sorted_by_size_of_net() -> None:
    """Sorted by net buying, a cut of 5 would keep only buyers and drop
    600547's −2.28 亿, the day's heaviest selling."""

    normalized = normalize_dragon_tiger_market(
        market_raw(rows_of("em_lhb_market_20260928.json"), 14),
        DragonTigerMarketRequest("2026-09-28", limit=5),
    )

    data = normalized.data
    # 14 rows but 8 stocks: 601218 and 920025 three times, 002342 and 002614
    # twice.
    assert (data["trade_date"], data["row_count"], data["stock_count"]) == (
        "2026-09-28",
        14,
        8,
    )
    items = cast(list[Json], data["items"])
    assert [(item["code"], item["net_amount"]) for item in items] == [
        ("000823", 379628841.78),
        ("600547", -227586786.02),
        ("002342", 76349006.77),
        ("601218", -69210216.27),
        ("601218", -69210216.27),
    ]
    assert items[0] == {
        "code": "000823",
        "symbol": "000823.SZ",
        "name": "超声电子",
        "reason": "日换手率达到20%的前5只证券",
        "close": 22.18,
        "change_percent": -9.3954,
        "turnover_rate": 26.9198,
        "buy_amount": 822386287.03,
        "sell_amount": 442757445.25,
        "net_amount": 379628841.78,
        "board": "深交所主板",
    }
    assert data["note"] is None
    assert normalized.warnings == (MARKET_BASIS,)
    assert "绝对值" in MARKET_BASIS
    assert normalized.degraded is False


def test_a_beijing_row_keeps_its_code_and_name_without_a_symbol() -> None:
    items = cast(
        list[Json],
        normalize_dragon_tiger_market(
            market_raw(rows_of("em_lhb_market_20260928.json")),
            DragonTigerMarketRequest("2026-09-28", limit=200),
        ).data["items"],
    )

    beijing = [item for item in items if item["code"] == "920025"]
    assert len(beijing) == 3
    assert {(item["symbol"], item["name"], item["board"]) for item in beijing} == {
        (None, "凯达重工", "北京证券交易所")
    }
    # The smallest net either way comes last.
    assert (items[-1]["code"], items[-1]["net_amount"]) == ("920025", -5146725.26)
    assert [item["symbol"] for item in items if item["code"] == "600340"] == [
        "600340.SH"
    ]


def test_a_row_without_a_net_sorts_last_and_a_stray_date_is_dropped() -> None:
    rows = copy.deepcopy(rows_of("em_lhb_market_20260928.json"))
    for key in ("BILLBOARD_NET_AMT", "NET_BS_AMT", "BILLBOARD_BUY_AMT", "SUM_BUY_AMT"):
        rows[0][key] = None
    rows[1]["TRADE_DATE"] = "2026-09-24 00:00:00"

    data = normalize_dragon_tiger_market(
        market_raw(rows), DragonTigerMarketRequest("2026-09-28", limit=200)
    ).data

    items = cast(list[Json], data["items"])
    assert data["row_count"] == 13
    assert items[-1]["code"] == rows[0]["SECURITY_CODE"]
    assert items[-1]["net_amount"] is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {"data": []},
        [],
        market_raw(
            [{"SECURITY_NAME_ABBR": "没有代码", "TRADE_DATE": "2026-09-28 00:00:00"}]
        ),
    ],
)
def test_an_unusable_market_answer_is_a_retryable_outage(raw: object) -> None:
    with pytest.raises(UpstreamUnavailable) as caught:
        normalize_dragon_tiger_market(raw, DragonTigerMarketRequest("2026-09-28"))

    assert caught.value.retryable is True
