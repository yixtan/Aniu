"""涨跌停池 and the sentiment summary, on the pools of 2026-09-28.

The fixtures are push2ex's and 同花顺's answers for that day, fetched after
the close (01:45 on the 29th): 33 涨停, 11 炸板, 56 跌停 (five kept), and the
52 stocks that were 涨停 the trading day before. The summary numbers were
checked by hand against them and against 同花顺's own day totals: 炸板率
25.0%, height 5, ladder {1: 26, 2: 3, 3: 3, 5: 1}, 晋级率 7/52 = 13.5%.

Lanes are named here as strings, never through the adapters' constants: a
test that reads the constant the code reads agrees with any value it is
changed to.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest

from backend.stock_api.public.contracts import LimitPoolRequest, LimitSummaryRequest
from backend.stock_api.public.errors import UpstreamUnavailable
from backend.stock_api.public.http import (
    LANE_REST_AFTER_FAILURES,
    LaneRest,
    PublicHttpTransport,
)
from backend.stock_api.public.normalizers.limit_pools import (
    POOL_BASIS,
    SUMMARY_BASIS,
    THS_NO_YESTERDAY,
    THS_POOL_BASIS,
    THS_SUMMARY_BASIS,
    normalize_limit_pool,
    normalize_limit_summary,
)
from backend.stock_api.public.providers.eastmoney import EastMoneyAdapter
from backend.stock_api.public.providers.sina import SinaAdapter
from backend.stock_api.public.providers.tencent import TencentAdapter
from backend.stock_api.public.providers.ths import ThsAdapter
from backend.stock_api.public.router import PublicProviderAdapters, PublicStockRouter
from backend.stock_api.public.service import bound_agent_result

_FIXTURES = Path(__file__).parent / "fixtures"
DAY = "2026-09-28"
POOLS = ("limit_up", "broken", "limit_down", "previous_limit_up")
PATHS = {
    "/getTopicZTPool": "limit_up",
    "/getTopicZBPool": "broken",
    "/getTopicDTPool": "limit_down",
    "/getYesterdayZTPool": "previous_limit_up",
}


def fixture(name: str) -> object:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def pool_fixture(pool: str) -> dict[str, object]:
    return cast(dict[str, object], fixture(f"em_pool_{pool}_20260928.json"))


def ths_fixture() -> dict[str, object]:
    return cast(dict[str, object], fixture("ths_pool_summary_20260928.json"))


def items_of(data: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], data["items"])


def codes(data: dict[str, object]) -> list[object]:
    return [item["code"] for item in items_of(data)]


def eastmoney_answering(
    answer: Callable[[httpx.Request], httpx.Response],
) -> tuple[EastMoneyAdapter, list[httpx.Request], PublicHttpTransport]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer(request)

    transport = PublicHttpTransport(transport=httpx.MockTransport(handler))
    transport._gates["eastmoney_push2ex"].minimum_start_interval = 0
    return EastMoneyAdapter(transport), seen, transport


def real_pools(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=pool_fixture(PATHS[request.url.path]))


def router_with(
    eastmoney: EastMoneyAdapter, ths_raw: object | None = None
) -> PublicStockRouter:
    async def limit_up_pool(*_: object, **__: object) -> object:
        return ths_raw

    return PublicStockRouter(
        PublicProviderAdapters(
            eastmoney=eastmoney,
            tencent=cast(TencentAdapter, SimpleNamespace()),
            sina=cast(SinaAdapter, SimpleNamespace()),
            ths=cast(ThsAdapter, SimpleNamespace(limit_up_pool=limit_up_pool)),
        )
    )


def with_data(pool: str, data: object) -> dict[str, object]:
    return {**pool_fixture(pool), "data": data}


# The adapter


@pytest.mark.parametrize(
    ("pool", "path", "sort"),
    [
        ("limit_up", "/getTopicZTPool", "fbt:asc"),
        ("broken", "/getTopicZBPool", "fbt:asc"),
        ("limit_down", "/getTopicDTPool", "fund:asc"),
        # Yesterday's pool is asked for with today's date.
        ("previous_limit_up", "/getYesterdayZTPool", "zs:desc"),
    ],
)
async def test_each_pool_is_asked_for_by_its_own_path(
    pool: str, path: str, sort: str
) -> None:
    adapter, seen, _ = eastmoney_answering(real_pools)

    raw = await adapter.limit_pool(
        LimitPoolRequest(pool, DAY),  # type: ignore[arg-type]
        timeout_seconds=1.0,
        cancellation_token=None,
    )

    assert raw == pool_fixture(pool)
    assert len(seen) == 1
    url = seen[0].url
    assert (url.host, url.path) == ("push2ex.eastmoney.com", path)
    assert dict(url.params) == {
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "dpt": "wz.ztzt",
        "Pageindex": "0",
        "pagesize": "10000",
        "sort": sort,
        "date": "20260928",
    }
    assert seen[0].headers["Referer"] == "https://quote.eastmoney.com/"


async def test_the_summary_asks_for_the_four_pools_one_after_another() -> None:
    adapter, seen, _ = eastmoney_answering(real_pools)

    raw = await adapter.limit_pools_all(
        LimitSummaryRequest(DAY), timeout_seconds=1.0, cancellation_token=None
    )

    assert [PATHS[request.url.path] for request in seen] == list(POOLS)
    assert raw == {pool: pool_fixture(pool) for pool in POOLS}


async def test_the_summary_stops_at_the_first_pool_that_fails() -> None:
    """A partial set would give a wrong 炸板率, and asking on would spend the
    lane's failure allowance on a call that is already lost."""

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/getTopicZBPool":
            return httpx.Response(403, text="Forbidden")
        return real_pools(request)

    adapter, seen, _ = eastmoney_answering(answer)

    with pytest.raises(UpstreamUnavailable) as caught:
        await adapter.limit_pools_all(
            LimitSummaryRequest(DAY), timeout_seconds=1.0, cancellation_token=None
        )

    assert caught.value.retryable is True
    assert [request.url.path for request in seen] == [
        "/getTopicZTPool",
        "/getTopicZBPool",
    ]


async def test_a_hang_up_costs_two_requests_not_eight() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("Server disconnected", request=request)

    adapter, seen, transport = eastmoney_answering(answer)

    with pytest.raises(UpstreamUnavailable) as caught:
        await adapter.limit_pools_all(
            LimitSummaryRequest(DAY), timeout_seconds=1.0, cancellation_token=None
        )

    assert caught.value.error_category == "network"
    assert [request.url.path for request in seen] == ["/getTopicZTPool"] * 2
    assert transport._lane_rest.remaining("eastmoney_push2ex") == 0


class RecordingRest(LaneRest):
    """A LaneRest that notes every lane the transport checks before sending."""

    def __init__(self) -> None:
        super().__init__(clock=lambda: 1000.0)
        self.asked: list[str] = []

    def remaining(self, lane: str) -> float:
        self.asked.append(lane)
        return LaneRest.remaining(self, lane)


def resting(rest: LaneRest, lane: str) -> None:
    for _ in range(LANE_REST_AFTER_FAILURES):
        rest.hung_up(lane)


async def test_the_pools_travel_on_the_push2ex_lane() -> None:
    rest = RecordingRest()
    transport = PublicHttpTransport(
        transport=httpx.MockTransport(real_pools), lane_rest=rest
    )
    for gate in transport._gates.values():
        gate.minimum_start_interval = 0
    adapter = EastMoneyAdapter(transport)

    await adapter.limit_pool(
        LimitPoolRequest("limit_up", DAY), timeout_seconds=1.0, cancellation_token=None
    )
    await adapter.limit_pools_all(
        LimitSummaryRequest(DAY), timeout_seconds=1.0, cancellation_token=None
    )

    assert set(rest.asked) == {"eastmoney_push2ex"}
    assert len(rest.asked) == 5


async def test_a_push2_rest_does_not_stop_the_pools() -> None:
    """push2 had been hanging up on this machine since 09-23 while push2ex
    answered 4 of 4; a rest of one must not silence the other."""

    rest = LaneRest(clock=lambda: 1000.0)
    for lane in ("eastmoney_push", "eastmoney", "eastmoney_f10"):
        resting(rest, lane)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return real_pools(request)

    transport = PublicHttpTransport(
        transport=httpx.MockTransport(handler), lane_rest=rest
    )
    transport._gates["eastmoney_push2ex"].minimum_start_interval = 0
    adapter = EastMoneyAdapter(transport)

    one = await adapter.limit_pool(
        LimitPoolRequest("limit_up", DAY), timeout_seconds=1.0, cancellation_token=None
    )
    four = await adapter.limit_pools_all(
        LimitSummaryRequest(DAY), timeout_seconds=1.0, cancellation_token=None
    )

    assert one == pool_fixture("limit_up")
    assert set(cast(dict[str, object], four)) == set(POOLS)
    assert len(seen) == 5


async def test_a_push2ex_rest_stops_the_pools_without_a_request() -> None:
    rest = LaneRest(clock=lambda: 1000.0)
    resting(rest, "eastmoney_push2ex")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return real_pools(request)

    adapter = EastMoneyAdapter(
        PublicHttpTransport(transport=httpx.MockTransport(handler), lane_rest=rest)
    )

    with pytest.raises(UpstreamUnavailable, match="push2ex") as caught:
        await adapter.limit_pool(
            LimitPoolRequest("limit_up", DAY),
            timeout_seconds=1.0,
            cancellation_token=None,
        )

    assert caught.value.retryable is True
    assert seen == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, text="Forbidden"),
        httpx.Response(404, text="Not Found"),
        httpx.Response(302, headers={"Location": "https://verify.example.test/"}),
        httpx.Response(
            200,
            text="<html><body>访问过于频繁</body></html>",
            headers={"Content-Type": "text/html"},
        ),
    ],
    ids=["403", "404", "redirect", "html"],
)
async def test_an_anti_bot_answer_leaves_retryable_after_one_request(
    response: httpx.Response,
) -> None:
    """The transport calls these final; the 同花顺 stand-in needs them not to be."""

    adapter, seen, _ = eastmoney_answering(lambda _: response)

    with pytest.raises(UpstreamUnavailable) as caught:
        await adapter.limit_pool(
            LimitPoolRequest("limit_up", DAY),
            timeout_seconds=1.0,
            cancellation_token=None,
        )

    assert caught.value.retryable is True
    assert len(seen) == 1


async def test_a_nonzero_rc_is_a_refusal_but_null_data_is_an_answer() -> None:
    refused, _, _ = eastmoney_answering(
        lambda _: httpx.Response(200, json={"rc": 102, "msg": "参数错误", "data": None})
    )
    with pytest.raises(UpstreamUnavailable) as caught:
        await refused.limit_pool(
            LimitPoolRequest("broken", DAY),
            timeout_seconds=1.0,
            cancellation_token=None,
        )
    assert caught.value.retryable is True
    assert caught.value.error_category == "business_failure"
    assert "参数错误" in str(caught.value) and "102" in str(caught.value)

    empty = {"rc": 0, "rt": 110, "svr": 1, "lt": 2, "full": 0, "data": None}
    answered, _, _ = eastmoney_answering(lambda _: httpx.Response(200, json=empty))
    assert (
        await answered.limit_pool(
            LimitPoolRequest("broken", DAY),
            timeout_seconds=1.0,
            cancellation_token=None,
        )
        == empty
    )


# Through the router


async def test_the_summary_through_the_router_reads_2026_09_28() -> None:
    adapter, _, _ = eastmoney_answering(real_pools)

    result = await router_with(adapter).execute(LimitSummaryRequest(DAY))

    meta = cast(dict[str, object], result["meta"])
    data = cast(dict[str, object], result["data"])
    assert (meta["source"], meta["fallback_used"], meta["degraded"]) == (
        "eastmoney",
        False,
        False,
    )
    assert meta["warnings"] == [SUMMARY_BASIS, LIMIT_DOWN_TRIMMED]
    assert (data["break_rate_percent"], data["promotion_rate_percent"]) == (
        25.0,
        13.5,
    )


async def test_a_blocked_limit_up_pool_hands_over_to_tonghuashun() -> None:
    adapter, seen, _ = eastmoney_answering(lambda _: httpx.Response(403))

    result = await router_with(adapter, ths_fixture()).execute(
        LimitPoolRequest("limit_up", DAY, limit=3)
    )

    meta = cast(dict[str, object], result["meta"])
    data = cast(dict[str, object], result["data"])
    assert len(seen) == 1
    assert (meta["source"], meta["fallback_used"], meta["degraded"]) == (
        "ths",
        True,
        True,
    )
    assert meta["warnings"] == ["东方财富暂时不可用，已使用同花顺。", THS_POOL_BASIS]
    assert (data["count"], codes(data)) == (33, ["600825", "603949", "600802"])


async def test_a_blocked_summary_hands_over_to_tonghuashun() -> None:
    adapter, _, _ = eastmoney_answering(
        lambda _: httpx.Response(200, text="<html>请完成验证</html>")
    )

    result = await router_with(adapter, ths_fixture()).execute(LimitSummaryRequest(DAY))

    meta = cast(dict[str, object], result["meta"])
    assert (meta["source"], meta["degraded"]) == ("ths", True)
    assert THS_NO_YESTERDAY in cast(list[str], meta["warnings"])


# East Money's normalizers

LIMIT_DOWN_TRIMMED = (
    "东方财富只返回了部分明细（跌停池 5 / 56），家数和炸板率按它给的总数。"
)
"""The fixture keeps five of the 跌停池's 56 rows and its ``tc`` of 56."""


def test_the_summary_of_2026_09_28() -> None:
    result = normalize_limit_summary(
        {pool: pool_fixture(pool) for pool in POOLS}, LimitSummaryRequest(DAY)
    )

    data = result.data
    assert {key: value for key, value in data.items() if key != "items"} == {
        "trade_date": DAY,
        "limit_up_count": 33,
        "broken_count": 11,
        # The fixture keeps five of the day's 56 rows; the count is push2ex's
        # own total, as 同花顺's day total also says.
        "limit_down_count": 56,
        "previous_limit_up_count": 52,
        "break_rate_percent": 25.0,
        "max_board_count": 5,
        "ladder": [
            {"boards": 5, "count": 1},
            {"boards": 3, "count": 3},
            {"boards": 2, "count": 3},
            {"boards": 1, "count": 26},
        ],
        "promoted_count": 7,
        "promotion_rate_percent": 13.5,
        "previous_limit_up_avg_change_percent": -1.95,
    }
    assert list(data)[-1] == "items"
    assert codes(data)[:7] == [
        "600825",
        "603949",
        "603396",
        "600802",
        "603278",
        "000678",
        "601218",
    ]
    assert len(items_of(data)) == 10
    assert items_of(data)[0] == {
        "code": "600825",
        "symbol": "600825.SH",
        "name": "新华传媒",
        "board_count": 5,
        "streak": "5天5板",
        "industry": "出版",
    }
    assert (result.degraded, result.warnings) == (
        False,
        (SUMMARY_BASIS, LIMIT_DOWN_TRIMMED),
    )


def test_the_bases_leave_st_out_and_give_st_no_limit_of_its_own() -> None:
    """Main-board ST stocks move 10%, like the rest: *ST华幸 1.17 → 1.29 on
    09-28. None is in East Money's pools, which is why its 33 涨停 were
    同花顺 归因's 35 less ST万邦 and *ST华幸."""

    for basis in (*POOL_BASIS.values(), SUMMARY_BASIS, THS_POOL_BASIS):
        assert "不含 ST" in basis
    assert "不含 ST" in THS_SUMMARY_BASIS
    assert "ST 的涨停幅度" not in SUMMARY_BASIS
    names = [
        cast(str, row["n"])
        for pool in POOLS
        for row in cast(
            list[dict[str, object]],
            cast(dict[str, object], pool_fixture(pool)["data"])["pool"],
        )
    ]
    assert not any("ST" in name for name in names)


def test_the_bases_hold_at_any_hour() -> None:
    """Read at 10:00, a pool is the moment, not the close."""

    for pool in ("limit_up", "broken", "limit_down"):
        assert "取数时" in POOL_BASIS[pool]
        assert "收盘封在" not in POOL_BASIS[pool]
    assert "取数时没封住" in SUMMARY_BASIS
    assert "今天收在" not in SUMMARY_BASIS


def test_counts_come_from_push2exs_own_total() -> None:
    result = normalize_limit_pool(
        pool_fixture("limit_down"), LimitPoolRequest("limit_down", DAY)
    )

    assert (result.data["count"], len(items_of(result.data))) == (56, 5)
    assert result.warnings == (
        POOL_BASIS["limit_down"],
        "东方财富只返回了 5 / 56 只跌停池的明细，count 按它给的总数，items 只有这些。",
    )


def test_a_cut_limit_up_pool_says_the_ladder_is_partial() -> None:
    raw = {pool: pool_fixture(pool) for pool in POOLS}
    raw["limit_down"] = with_data(
        "limit_down",
        {**cast(dict[str, object], pool_fixture("limit_down")["data"]), "tc": 5},
    )
    limit_up = copy.deepcopy(pool_fixture("limit_up"))
    cast(dict[str, object], limit_up["data"])["tc"] = 150
    raw["limit_up"] = limit_up

    result = normalize_limit_summary(raw, LimitSummaryRequest(DAY))

    assert result.data["limit_up_count"] == 150
    assert result.data["break_rate_percent"] == round(11 / 161 * 100, 1)
    assert result.warnings[-1] == (
        "东方财富只返回了部分明细（涨停池 33 / 150），家数和炸板率按它给的总数。"
        "连板梯队和高度只按返回的涨停股算，可能偏低。"
    )


def test_a_cut_yesterdays_pool_gives_no_promotion_rate() -> None:
    """Sent best first, so a cut page would overstate the 晋级率."""

    raw = {pool: pool_fixture(pool) for pool in POOLS}
    raw["limit_down"] = with_data(
        "limit_down",
        {**cast(dict[str, object], pool_fixture("limit_down")["data"]), "tc": 5},
    )
    previous = copy.deepcopy(pool_fixture("previous_limit_up"))
    cast(dict[str, object], previous["data"])["tc"] = 80
    raw["previous_limit_up"] = previous

    data = normalize_limit_summary(raw, LimitSummaryRequest(DAY)).data

    assert data["previous_limit_up_count"] == 80
    assert (
        data["promoted_count"],
        data["promotion_rate_percent"],
        data["previous_limit_up_avg_change_percent"],
    ) == (None, None, None)


def test_every_pool_on_another_day_is_an_empty_summary_not_a_mismatch() -> None:
    """push2ex answering 09-24 for all four is "no data for 09-28", which the
    tool reads as "ask the day before"; only a mix of days is an error."""

    raw = {
        pool: with_data(pool, {"tc": 3, "qdate": 20260924, "pool": [{"c": "600487"}]})
        for pool in POOLS
    }

    data = normalize_limit_summary(raw, LimitSummaryRequest(DAY)).data

    assert all(value == 0 for key, value in data.items() if key.endswith("_count"))
    assert (data["items"], data["ladder"]) == ([], [])
    assert "返回的是 2026-09-24" in cast(str, data["note"])


def test_promotion_is_a_close_at_todays_limit_not_a_big_gain() -> None:
    """301699 洛轴股份 rose 14.46% on a 20% limit; the ≥ 9.8% rule counts it."""

    rows = cast(
        list[dict[str, object]],
        cast(dict[str, object], pool_fixture("previous_limit_up")["data"])["pool"],
    )
    by_gain = [row["c"] for row in rows if cast(float, row["zdp"]) >= 9.8]

    result = normalize_limit_summary(
        {pool: pool_fixture(pool) for pool in POOLS}, LimitSummaryRequest(DAY)
    )

    assert len(by_gain) == 8 and "301699" in by_gain
    assert result.data["promoted_count"] == 7


def test_the_limit_up_pool_puts_the_highest_boards_first() -> None:
    result = normalize_limit_pool(
        pool_fixture("limit_up"), LimitPoolRequest("limit_up", DAY, limit=4)
    )

    data = result.data
    assert (data["count"], len(items_of(data))) == (33, 4)
    assert [
        (item["code"], item["board_count"], item["first_seal_time"])
        for item in items_of(data)
    ] == [
        ("600825", 5, "09:25:01"),
        ("603949", 3, "09:30:00"),
        ("603396", 3, "09:30:02"),
        ("600802", 3, "09:34:41"),
    ]
    assert items_of(data)[0] == {
        "code": "600825",
        "symbol": "600825.SH",
        "name": "新华传媒",
        "price": 8.55,
        "change_percent": 10.04,
        "amount": 42985476,
        "float_market_value": 8933791118,
        "turnover_rate": 0.48,
        "board_count": 5,
        "streak": "5天5板",
        "seal_fund": 2386155340,
        "first_seal_time": "09:25:01",
        "last_seal_time": "09:25:01",
        "broken_times": 0,
        "limit_price": None,
        "yesterday_board_count": None,
        "industry": "出版",
    }
    assert "note" not in data
    assert result.warnings == (POOL_BASIS["limit_up"],)


def test_the_broken_pool_runs_by_first_seal_and_keeps_the_limit_price() -> None:
    data = normalize_limit_pool(
        pool_fixture("broken"), LimitPoolRequest("broken", DAY)
    ).data

    first = items_of(data)[0]
    assert (data["count"], first["code"], first["first_seal_time"]) == (
        11,
        "000790",
        "09:25:00",
    )
    assert (first["price"], first["limit_price"], first["streak"]) == (
        4.42,
        5.15,
        "2天1板",
    )
    seals = [item["first_seal_time"] for item in items_of(data)]
    assert seals == sorted(cast(list[str], seals))
    # zttj {0, 0}: no 涨停 in the window, which is not missing data.
    assert next(i for i in items_of(data) if i["code"] == "600540")["streak"] is None
    assert {item["board_count"] for item in items_of(data)} == {None}


def test_the_limit_down_pool_runs_by_seal_fund_and_drops_fba() -> None:
    data = normalize_limit_pool(
        pool_fixture("limit_down"), LimitPoolRequest("limit_down", DAY)
    ).data

    assert codes(data) == ["600487", "603230", "600664", "002796", "000988"]
    first, _, twice = items_of(data)[:3]
    assert (first["seal_fund"], first["broken_times"], first["last_seal_time"]) == (
        1324064091,
        4,
        "09:55:10",
    )
    assert twice["board_count"] == 2
    assert not any("fba" in item or "pe" in item for item in items_of(data))


def test_yesterdays_pool_keeps_a_beijing_stock_without_a_symbol() -> None:
    data = normalize_limit_pool(
        pool_fixture("previous_limit_up"),
        LimitPoolRequest("previous_limit_up", DAY, limit=200),
    ).data

    items = items_of(data)
    assert (data["count"], len(items)) == (52, 52)
    assert items[0]["code"] == "301699"
    assert (items[1]["code"], items[1]["price"], items[1]["limit_price"]) == (
        "600825",
        8.55,
        8.55,
    )
    assert items[1]["yesterday_board_count"] == 4
    assert (items[-1]["code"], items[-1]["name"], items[-1]["symbol"]) == (
        "920748",
        "路桥信息",
        None,
    )


def test_yesterdays_pool_cut_keeps_both_ends() -> None:
    """The re-sealed names and the 大面 are what matter; the flat middle goes."""

    full = items_of(
        normalize_limit_pool(
            pool_fixture("previous_limit_up"),
            LimitPoolRequest("previous_limit_up", DAY, limit=200),
        ).data
    )

    data = normalize_limit_pool(
        pool_fixture("previous_limit_up"),
        LimitPoolRequest("previous_limit_up", DAY, limit=5),
    ).data

    assert data["count"] == 52
    assert codes(data) == [item["code"] for item in (*full[:3], *full[-2:])]
    changes = [cast(float, item["change_percent"]) for item in items_of(data)]
    assert changes == sorted(changes, reverse=True)
    assert changes[-1] == min(cast(float, item["change_percent"]) for item in full)


@pytest.mark.parametrize(
    ("data", "note"),
    [
        (None, "非交易日、还没开盘，或当天该池为 0 只"),
        ({"tc": 0, "qdate": 20260928, "pool": []}, "的跌停池没有股票"),
        ({"tc": 0, "qdate": 20260928}, "的跌停池没有股票"),
        ({"tc": 56, "qdate": 20260924, "pool": [{"c": "600487"}]}, "按空处理"),
    ],
    ids=["null", "no_rows", "no_list", "another_day"],
)
def test_an_empty_pool_is_a_success_with_a_note(data: object, note: str) -> None:
    result = normalize_limit_pool(
        with_data("limit_down", data), LimitPoolRequest("limit_down", DAY)
    )

    assert (result.data["count"], result.data["items"]) == (0, [])
    assert note in cast(str, result.data["note"])


def test_a_day_without_pools_is_an_empty_summary() -> None:
    """Zero counts and no items: what the tool reads as "try the day before"."""

    result = normalize_limit_summary(
        {pool: with_data(pool, None) for pool in POOLS}, LimitSummaryRequest(DAY)
    )

    data = result.data
    assert data["items"] == [] and data["ladder"] == []
    assert all(value == 0 for key, value in data.items() if key.endswith("_count"))
    assert (data["break_rate_percent"], data["promotion_rate_percent"]) == (0.0, None)
    assert "可能不是交易日" in cast(str, data["note"])


def test_a_session_not_yet_open_says_so() -> None:
    raw: dict[str, object] = {pool: with_data(pool, None) for pool in POOLS[:3]}
    raw["previous_limit_up"] = pool_fixture("previous_limit_up")

    data = normalize_limit_summary(raw, LimitSummaryRequest(DAY)).data

    assert (data["limit_up_count"], data["previous_limit_up_count"]) == (0, 52)
    assert "还没开盘" in cast(str, data["note"])


def test_pools_from_different_days_are_not_summed() -> None:
    raw: dict[str, object] = {pool: pool_fixture(pool) for pool in POOLS}
    raw["broken"] = with_data("broken", {"tc": 3, "qdate": 20260924, "pool": []})

    with pytest.raises(UpstreamUnavailable, match="日期不一致"):
        normalize_limit_summary(raw, LimitSummaryRequest(DAY))


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {"rc": 0, "data": []},
        {"rc": 0, "data": {"tc": 1, "qdate": 20260928, "pool": {"c": "600487"}}},
        {"rc": 0, "data": {"tc": 5, "qdate": 20260928}},
        {"rc": 0, "data": {"tc": 1, "qdate": 20260928, "pool": [{"n": "无代码"}]}},
    ],
    ids=["root_list", "data_list", "pool_object", "rows_missing", "no_codes"],
)
def test_a_malformed_pool_is_an_upstream_failure(raw: object) -> None:
    with pytest.raises(UpstreamUnavailable):
        normalize_limit_pool(raw, LimitPoolRequest("limit_up", DAY))


def test_a_summary_missing_a_pool_is_an_upstream_failure() -> None:
    raw = {pool: pool_fixture(pool) for pool in POOLS[:3]}

    with pytest.raises(UpstreamUnavailable):
        normalize_limit_summary(raw, LimitSummaryRequest(DAY))


def test_a_bigger_limit_never_returns_fewer_pool_rows() -> None:
    """Each pool item is about 350 characters; 200 of them run past the
    64,000-character bound, which used to halve them to 100."""

    rows = cast(
        list[dict[str, object]],
        cast(dict[str, object], pool_fixture("limit_up")["data"])["pool"],
    )
    many = [
        {**rows[index % len(rows)], "c": f"{600000 + index:06d}"}
        for index in range(400)
    ]
    raw = with_data("limit_up", {"tc": 400, "qdate": 20260928, "pool": many})

    def returned(limit: int) -> int:
        request = LimitPoolRequest("limit_up", DAY, limit=limit)
        result = {
            "data": normalize_limit_pool(raw, request).data,
            "meta": {"warnings": []},
        }
        bounded = bound_agent_result(result, request)
        return len(items_of(cast(dict[str, object], bounded["data"])))

    counts = [returned(limit) for limit in (150, 180, 185, 200)]
    assert counts == sorted(counts)
    assert counts[0] == 150
    assert counts[-1] > 150
