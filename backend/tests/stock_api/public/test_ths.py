"""同花顺: 涨停归因, its 涨停揭秘 fallback, and the 人气热榜.

The fixtures are real responses for Monday 2026-09-28, fetched in the small
hours of the 29th and cut to a few rows. That day was weak: 33 closed at the
limit up and 56 at the limit down. getharden listed 35, because it keeps the
ST names 涨停揭秘 leaves out (002082 ST万邦, 600340 *ST华幸). Two of
getharden's units are not what SKILL's table says: 成交额 is in 万元 and 成交量
in 手.
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

from backend.stock_api.public.contracts import (
    HotListRequest,
    LimitPoolRequest,
    LimitReasonsRequest,
)
from backend.stock_api.public.errors import NoStockData, UpstreamUnavailable
from backend.stock_api.public.http import (
    LANE_REST_AFTER_FAILURES,
    LaneRest,
    PublicHttpTransport,
)
from backend.stock_api.public.normalizers.ths import (
    HARDEN_BASIS,
    HOT_LIST_BASIS,
    HOT_LIST_SNAPSHOT_NOTE,
    REASONS_FALLBACK,
    normalize_ths_harden,
    normalize_ths_hot_list,
    normalize_ths_limit_reasons,
)
from backend.stock_api.public.providers.eastmoney import EastMoneyAdapter
from backend.stock_api.public.providers.sina import SinaAdapter
from backend.stock_api.public.providers.tencent import TencentAdapter
from backend.stock_api.public.providers.ths import ThsAdapter
from backend.stock_api.public.router import PublicProviderAdapters, PublicStockRouter

_FIXTURES = Path(__file__).parent / "fixtures"
HARDEN = "https://zx.10jqka.com.cn/event/api/getharden/date/2026-09-28/orderby/date/orderway/desc/charset/GBK/"
HARDEN_HTTP = HARDEN.replace("https://", "http://")
REASONS = LimitReasonsRequest("2026-09-28")

Handler = Callable[[httpx.Request], httpx.Response]


def fixture(name: str) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads((_FIXTURES / name).read_text(encoding="utf-8")),
    )


def harden_payload() -> dict[str, object]:
    return fixture("ths_getharden_20260928.json")


def pool_data() -> dict[str, object]:
    return cast(dict[str, object], fixture("ths_limit_up_pool_20260928.json")["data"])


def hot_data() -> dict[str, object]:
    return cast(dict[str, object], fixture("ths_hot_list_day.json")["data"])


def items_of(data: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], data["items"])


def ths_adapter(handler: Handler) -> tuple[ThsAdapter, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = PublicHttpTransport(transport=httpx.MockTransport(record))
    transport._gates["ths"].minimum_start_interval = 0
    return ThsAdapter(transport), seen


def ascii_json(payload: object) -> httpx.Response:
    """getharden's own form: ASCII with \\uXXXX escapes, as on 2026-09-28."""

    return httpx.Response(200, content=json.dumps(payload).encode("ascii"))


def html_page(_: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        text="<html><body>请先登录</body></html>",
        headers={"Content-Type": "text/html"},
    )


# --- adapter ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_harden_asks_https_once_and_reads_gbk() -> None:
    payload = harden_payload()
    body = json.dumps(payload, ensure_ascii=False).encode("gbk")
    adapter, seen = ths_adapter(lambda _: httpx.Response(200, content=body))

    raw = await adapter.harden(REASONS, timeout_seconds=1, cancellation_token=None)

    assert raw == payload
    assert [str(request.url) for request in seen] == [HARDEN]
    assert "Cookie" not in seen[0].headers and "Referer" not in seen[0].headers


@pytest.mark.asyncio
async def test_harden_moves_to_http_when_https_redirects() -> None:
    """A redirect is final to the transport; the adapter asks http itself."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"Location": HARDEN_HTTP})
        return ascii_json(harden_payload())

    adapter, seen = ths_adapter(handler)

    raw = await adapter.harden(REASONS, timeout_seconds=1, cancellation_token=None)

    assert cast(dict[str, object], raw)["date"] == "2026-09-28"
    assert [str(request.url) for request in seen] == [HARDEN, HARDEN_HTTP]


@pytest.mark.asyncio
async def test_harden_moves_to_http_when_https_will_not_connect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            raise httpx.ConnectError("tls handshake failed", request=request)
        return ascii_json(harden_payload())

    adapter, seen = ths_adapter(handler)

    await adapter.harden(REASONS, timeout_seconds=1, cancellation_token=None)

    assert [str(request.url) for request in seen] == [HARDEN, HARDEN_HTTP]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [
        lambda _: httpx.Response(403, text="forbidden"),
        lambda _: httpx.Response(404),
        lambda _: httpx.Response(301, headers={"Location": "https://10jqka.com.cn/"}),
        html_page,
    ],
    ids=["403", "404", "redirect", "html"],
)
async def test_harden_refusals_leave_the_fallback_open(handler: Handler) -> None:
    """The router stops at a final error; 涨停揭秘 must still get its turn."""

    adapter, seen = ths_adapter(handler)

    with pytest.raises(UpstreamUnavailable) as caught:
        await adapter.harden(REASONS, timeout_seconds=1, cancellation_token=None)

    assert caught.value.retryable is True
    assert str(caught.value).startswith("同花顺没有给出可用的数据")
    assert [request.url.scheme for request in seen] == ["https", "http"]


@pytest.mark.asyncio
async def test_harden_error_code_is_read_under_its_misspelled_key() -> None:
    adapter, _ = ths_adapter(
        lambda _: ascii_json({"errocode": 1, "errormsg": "参数错误", "data": []})
    )

    with pytest.raises(UpstreamUnavailable, match="参数错误") as caught:
        await adapter.harden(REASONS, timeout_seconds=1, cancellation_token=None)

    assert caught.value.retryable is True
    assert caught.value.error_category == "business_failure"


@pytest.mark.asyncio
async def test_limit_up_pool_returns_the_data_object_unchanged() -> None:
    envelope = fixture("ths_limit_up_pool_20260928.json")
    adapter, seen = ths_adapter(lambda _: httpx.Response(200, json=envelope))

    raw = await adapter.limit_up_pool(
        LimitPoolRequest("limit_up", "2026-09-28"),
        timeout_seconds=1,
        cancellation_token=None,
    )

    assert raw == envelope["data"]
    url = seen[0].url
    assert (url.host, url.path) == (
        "data.10jqka.com.cn",
        "/dataapi/limit_up/limit_up_pool",
    )
    assert url.params["date"] == "20260928"
    assert url.params["page"] == "1" and url.params["limit"] == "200"
    assert url.params["filter"] == "HS,GEM2STAR"
    assert url.params["field"].startswith("199112,10,9001,")


@pytest.mark.asyncio
async def test_limit_up_pool_reads_every_page_of_a_strong_day() -> None:
    """Counts come from page one's aggregates; later pages only add rows, and
    no more than five are fetched."""

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        data = {
            "page": {"limit": 200, "total": 1800, "count": 9, "page": page},
            "info": [{"code": f"60000{page}", "name": f"第{page}页"}],
            "date": "20260928",
        }
        return httpx.Response(200, json={"status_code": 0, "data": data})

    adapter, seen = ths_adapter(handler)

    raw = cast(
        dict[str, object],
        await adapter.limit_up_pool(
            REASONS, timeout_seconds=1, cancellation_token=None
        ),
    )

    assert [request.url.params["page"] for request in seen] == ["1", "2", "3", "4", "5"]
    assert [row["code"] for row in cast(list[dict[str, str]], raw["info"])] == [
        "600001",
        "600002",
        "600003",
        "600004",
        "600005",
    ]
    assert cast(dict[str, int], raw["page"])["total"] == 1800


@pytest.mark.asyncio
async def test_limit_up_pool_envelope_failures_are_retryable() -> None:
    adapter, _ = ths_adapter(
        lambda _: httpx.Response(200, json={"status_code": -1, "status_msg": "busy"})
    )
    with pytest.raises(UpstreamUnavailable, match="busy") as caught:
        await adapter.limit_up_pool(REASONS, timeout_seconds=1, cancellation_token=None)
    assert caught.value.retryable is True

    adapter, _ = ths_adapter(lambda _: httpx.Response(200, json={"status_code": 0}))
    assert (
        await adapter.limit_up_pool(REASONS, timeout_seconds=1, cancellation_token=None)
        == {}
    )

    adapter, _ = ths_adapter(html_page)
    with pytest.raises(UpstreamUnavailable) as caught:
        await adapter.limit_up_pool(REASONS, timeout_seconds=1, cancellation_token=None)
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_hot_list_asks_for_the_day_list() -> None:
    envelope = fixture("ths_hot_list_day.json")
    adapter, seen = ths_adapter(lambda _: httpx.Response(200, json=envelope))

    raw = await adapter.hot_list(
        HotListRequest(), timeout_seconds=1, cancellation_token=None
    )

    assert raw == envelope["data"]
    url = seen[0].url
    assert (url.host, url.path) == (
        "dq.10jqka.com.cn",
        "/fuyao/hot_list_data/out/hot_list/v1/stock",
    )
    assert dict(url.params) == {
        "stock_type": "a",
        "type": "day",
        "list_type": "normal",
    }


@pytest.mark.asyncio
async def test_hot_list_refusal_is_retryable() -> None:
    adapter, seen = ths_adapter(lambda _: httpx.Response(401))

    with pytest.raises(UpstreamUnavailable) as caught:
        await adapter.hot_list(
            HotListRequest(), timeout_seconds=1, cancellation_token=None
        )

    assert caught.value.retryable is True
    assert len(seen) == 1


# --- normalizers -----------------------------------------------------------


def test_harden_converts_units_and_splits_reasons() -> None:
    result = normalize_ths_harden(harden_payload(), REASONS)

    assert (result.degraded, result.warnings) == (False, (HARDEN_BASIS,))
    data = result.data
    assert (data["trade_date"], data["count"]) == ("2026-09-28", 7)
    by_code = {item["code"]: item for item in items_of(data)}
    # 410456 手 × 100 × 6.36 元 ≈ 26105 万元.
    assert by_code["001330"] == {
        "code": "001330",
        "symbol": "001330.SZ",
        "name": "博纳影业",
        "reasons": ["首部AI电影上映", "《三星堆：未来往事》"],
        "reason_text": "首部AI电影上映+《三星堆：未来往事》",
        "price": 6.36,
        "change_percent": 10.035,
        "turnover_rate": 3.53,
        "amount": 261_050_000,
        "volume_shares": 41_045_600,
    }
    assert by_code["600825"]["reasons"] == [
        "拟收购界面财联社",
        "上海国资",
        "“华”字辈",
    ]
    # ST names are in getharden; market 22 does not decide the exchange.
    assert by_code["600340"]["symbol"] == "600340.SH"
    assert by_code["002082"]["name"] == "ST万邦"
    assert by_code["688244"]["change_percent"] == 20


def test_harden_puts_the_heaviest_traded_first_and_counts_before_the_cut() -> None:
    result = normalize_ths_harden(
        harden_payload(), LimitReasonsRequest("2026-09-28", limit=3)
    )

    assert [item["code"] for item in items_of(result.data)] == [
        "600418",
        "603396",
        "002082",
    ]
    assert result.data["count"] == 7


def test_harden_keeps_codes_it_has_no_symbol_for() -> None:
    payload = harden_payload()
    rows = cast(list[dict[str, object]], payload["data"])
    rows.append({**rows[0], "code": "920118", "name": "北交所样例"})

    items = items_of(normalize_ths_harden(payload, REASONS).data)

    beijing = next(item for item in items if item["code"] == "920118")
    assert (beijing["symbol"], beijing["name"]) == (None, "北交所样例")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"data": []}, "还没有 2026-09-28 的名单"),
        ({"data": None}, "还没有 2026-09-28 的名单"),
        ({"date": "2026-09-24"}, "返回的是 2026-09-24 的名单"),
    ],
)
def test_harden_hands_over_when_it_has_nothing_for_the_day(
    change: dict[str, object], message: str
) -> None:
    """NoStockData, not an empty success: 涨停揭秘 is live, getharden is
    filled in by hand, so the fallback may well have the day."""

    with pytest.raises(NoStockData, match=message):
        normalize_ths_harden({**harden_payload(), **change}, REASONS)


def test_harden_rejects_a_malformed_answer() -> None:
    with pytest.raises(UpstreamUnavailable):
        normalize_ths_harden([], REASONS)
    with pytest.raises(UpstreamUnavailable, match="列表格式"):
        normalize_ths_harden({**harden_payload(), "data": {"rows": []}}, REASONS)
    with pytest.raises(UpstreamUnavailable, match="证券代码"):
        normalize_ths_harden({**harden_payload(), "data": [{"code": "x"}]}, REASONS)


@pytest.mark.usefixtures("host_zone_is_not_beijing")
def test_limit_reasons_fallback_reads_the_pool_as_the_same_shape() -> None:
    result = normalize_ths_limit_reasons(pool_data(), REASONS)

    assert result.degraded is True
    assert result.warnings == (REASONS_FALLBACK,)
    assert "不含 ST" in REASONS_FALLBACK
    data = result.data
    assert (data["trade_date"], data["count"]) == ("2026-09-28", 8)
    items = items_of(data)
    # Consecutive boards first, as the 涨停池 counts them, then who sealed
    # first; the row with no label (603396) last. 600418 (4天3板) and 002912
    # (4天2板) were 1连板 at East Money that day, so they sit with the 首板
    # rows, not above the real 连板.
    assert [item["code"] for item in items] == [
        "600825",
        "603949",
        "600802",
        "002912",
        "601579",
        "600418",
        "001368",
        "603396",
    ]
    first = items[0]
    assert first == {
        "code": "600825",
        "symbol": "600825.SH",
        "name": "新华传媒",
        "reasons": ["拟收购界面财联社", "上海国资", "“华”字辈"],
        "reason_text": "拟收购界面财联社+上海国资+“华”字辈",
        "price": 8.55,
        "change_percent": 10.0386,
        "turnover_rate": 0.4812,
        "amount": None,
        "volume_shares": None,
        "streak": "5天5板",
        "board_type": "一字板",
        "first_seal_time": "09:25:01",
        "last_seal_time": "09:25:01",
        "broken_times": 0,
        "seal_fund": 2_386_155_000,
    }
    kuaiji = next(item for item in items if item["code"] == "601579")
    assert (kuaiji["first_seal_time"], kuaiji["last_seal_time"]) == (
        "09:30:22",
        "15:00:00",
    )
    assert kuaiji["broken_times"] == 4
    assert items[-1]["streak"] is None


def test_limit_reasons_fallback_counts_before_the_cut() -> None:
    result = normalize_ths_limit_reasons(
        pool_data(), LimitReasonsRequest("2026-09-28", limit=3)
    )

    assert result.data["count"] == 8
    assert [item["code"] for item in items_of(result.data)] == [
        "600825",
        "603949",
        "600802",
    ]


@pytest.mark.parametrize(
    ("change", "note"),
    [
        ({"info": []}, "没有 2026-09-28 的涨停股"),
        ({"info": None}, "没有 2026-09-28 的涨停股"),
        ({"date": "20260924"}, "返回的是 2026-09-24 的名单"),
    ],
)
def test_limit_reasons_fallback_answers_an_empty_day_with_a_note(
    change: dict[str, object], note: str
) -> None:
    """The last candidate: empty is a success the tool can retry a day back
    from — items [] and every count 0."""

    result = normalize_ths_limit_reasons({**pool_data(), **change}, REASONS)

    assert result.data["items"] == [] and result.data["count"] == 0
    assert note in cast(str, result.data["note"])
    assert result.degraded is True


def test_limit_reasons_fallback_rejects_a_malformed_answer() -> None:
    with pytest.raises(UpstreamUnavailable):
        normalize_ths_limit_reasons(None, REASONS)
    with pytest.raises(UpstreamUnavailable, match="证券代码"):
        normalize_ths_limit_reasons(
            {**pool_data(), "info": [{"name": "无代码"}]}, REASONS
        )


def test_hot_list_keeps_rank_order_tags_and_labels() -> None:
    result = normalize_ths_hot_list(hot_data(), HotListRequest(limit=4))

    assert (result.degraded, result.warnings) == (False, (HOT_LIST_BASIS,))
    data = result.data
    assert data["fetched_at_note"] == HOT_LIST_SNAPSHOT_NOTE
    assert data["count"] == 6
    items = items_of(data)
    assert [item["rank"] for item in items] == [1, 2, 3, 4]
    assert items[0] == {
        "rank": 1,
        "code": "601091",
        "symbol": "601091.SH",
        "name": "沈鼓集团",
        "change_percent": 2.6296,
        "tags": ["注册制次新股", "煤化工概念"],
        "streak_label": "持续上榜",
        "heat": 6_191_304,
    }
    # 300308 carries no popularity_tag key at all.
    assert (items[2]["code"], items[2]["streak_label"]) == ("300308", None)
    assert items[3]["streak_label"] == "5天5板"


def test_hot_list_sorts_by_rank_not_by_arrival() -> None:
    data = hot_data()
    rows = cast(list[dict[str, object]], data["stock_list"])
    data["stock_list"] = list(reversed(rows))

    items = items_of(normalize_ths_hot_list(data, HotListRequest()).data)

    assert [item["rank"] for item in items] == [1, 2, 3, 4, 5, 33]
    assert items[-1]["streak_label"] == "首板涨停"


def test_hot_list_empty_or_malformed_is_not_a_quiet_day() -> None:
    with pytest.raises(NoStockData):
        normalize_ths_hot_list({"stock_list": []}, HotListRequest())
    with pytest.raises(UpstreamUnavailable):
        normalize_ths_hot_list({}, HotListRequest())
    with pytest.raises(UpstreamUnavailable):
        normalize_ths_hot_list([], HotListRequest())
    with pytest.raises(UpstreamUnavailable, match="证券代码"):
        normalize_ths_hot_list({"stock_list": [{"order": 1}]}, HotListRequest())


# --- through the router ----------------------------------------------------


def ths_router(handler: Handler) -> tuple[PublicStockRouter, list[httpx.Request]]:
    adapter, seen = ths_adapter(handler)
    router = PublicStockRouter(
        PublicProviderAdapters(
            eastmoney=cast(EastMoneyAdapter, SimpleNamespace()),
            tencent=cast(TencentAdapter, SimpleNamespace()),
            sina=cast(SinaAdapter, SimpleNamespace()),
            ths=adapter,
        )
    )
    return router, seen


def by_host(harden: Handler) -> Handler:
    envelope = fixture("ths_limit_up_pool_20260928.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "zx.10jqka.com.cn":
            return harden(request)
        assert request.url.host == "data.10jqka.com.cn"
        return httpx.Response(200, json=copy.deepcopy(envelope))

    return handler


@pytest.mark.asyncio
async def test_reasons_come_from_getharden_when_it_answers() -> None:
    router, seen = ths_router(by_host(lambda _: ascii_json(harden_payload())))

    result = await router.execute(REASONS)

    meta = cast(dict[str, object], result["meta"])
    assert (meta["source"], meta["fallback_used"], meta["degraded"]) == (
        "ths",
        False,
        False,
    )
    assert meta["warnings"] == [HARDEN_BASIS]
    assert [request.url.host for request in seen] == ["zx.10jqka.com.cn"]


@pytest.mark.asyncio
async def test_a_blocked_getharden_falls_back_to_the_pool() -> None:
    router, seen = ths_router(by_host(lambda _: httpx.Response(403)))

    result = await router.execute(REASONS)

    meta = cast(dict[str, object], result["meta"])
    assert (meta["source"], meta["fallback_used"], meta["degraded"]) == (
        "ths",
        True,
        True,
    )
    # Not 「同花顺暂时不可用，已使用同花顺」: one source, two of its endpoints.
    assert meta["warnings"] == [
        "同花顺的首选接口暂时不可用，已改用它的备用接口。",
        REASONS_FALLBACK,
    ]
    data = cast(dict[str, object], result["data"])
    assert data["count"] == 8
    assert [request.url.host for request in seen] == [
        "zx.10jqka.com.cn",
        "zx.10jqka.com.cn",
        "data.10jqka.com.cn",
    ]


@pytest.mark.asyncio
async def test_an_empty_getharden_falls_back_to_the_pool() -> None:
    router, _ = ths_router(
        by_host(
            lambda _: ascii_json(
                {"errocode": 0, "errormsg": "", "data": [], "date": "2026-09-28"}
            )
        )
    )

    result = await router.execute(REASONS)

    meta = cast(dict[str, object], result["meta"])
    assert meta["warnings"] == [
        "同花顺的首选接口未返回有效数据，已改用它的备用接口。",
        REASONS_FALLBACK,
    ]
    assert cast(dict[str, object], result["data"])["count"] == 8


@pytest.mark.asyncio
async def test_hot_list_through_the_router() -> None:
    envelope = fixture("ths_hot_list_day.json")
    router, _ = ths_router(lambda _: httpx.Response(200, json=envelope))

    result = await router.execute(HotListRequest(limit=2))

    meta = cast(dict[str, object], result["meta"])
    assert (meta["source"], meta["degraded"]) == ("ths", False)
    items = items_of(cast(dict[str, object], result["data"]))
    assert [item["code"] for item in items] == ["601091", "000592"]


# --- lanes -----------------------------------------------------------------


class RecordingRest(LaneRest):
    """A LaneRest that notes every lane the transport checks before sending."""

    def __init__(self) -> None:
        super().__init__(clock=lambda: 1000.0)
        self.asked: list[str] = []

    def remaining(self, lane: str) -> float:
        self.asked.append(lane)
        return LaneRest.remaining(self, lane)


def every_endpoint(request: httpx.Request) -> httpx.Response:
    if request.url.host == "zx.10jqka.com.cn":
        return ascii_json(harden_payload())
    if request.url.host == "dq.10jqka.com.cn":
        return httpx.Response(200, json=fixture("ths_hot_list_day.json"))
    return httpx.Response(200, json=fixture("ths_limit_up_pool_20260928.json"))


@pytest.mark.asyncio
async def test_every_tonghuashun_host_travels_on_the_ths_lane() -> None:
    """Named as a string: a test reading the adapter's own constant would
    agree with whatever it was changed to."""

    rest = RecordingRest()
    transport = PublicHttpTransport(
        transport=httpx.MockTransport(every_endpoint), lane_rest=rest
    )
    for gate in transport._gates.values():
        gate.minimum_start_interval = 0
    adapter = ThsAdapter(transport)

    await adapter.harden(REASONS, timeout_seconds=1.0, cancellation_token=None)
    await adapter.limit_up_pool(REASONS, timeout_seconds=1.0, cancellation_token=None)
    await adapter.hot_list(
        HotListRequest(), timeout_seconds=1.0, cancellation_token=None
    )

    assert set(rest.asked) == {"ths"}
    # The 1-a-second gate is the one that paces them.
    assert transport._gates["ths"].minimum_start_interval == 0
    assert PublicHttpTransport()._gates["ths"].minimum_start_interval == 1.0


@pytest.mark.asyncio
async def test_a_ths_rest_stops_every_host_and_an_east_money_rest_none() -> None:
    rest = LaneRest(clock=lambda: 1000.0)
    for lane in ("eastmoney", "eastmoney_f10", "eastmoney_push", "eastmoney_push2ex"):
        for _ in range(LANE_REST_AFTER_FAILURES):
            rest.hung_up(lane)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return every_endpoint(request)

    transport = PublicHttpTransport(
        transport=httpx.MockTransport(handler), lane_rest=rest
    )
    transport._gates["ths"].minimum_start_interval = 0
    adapter = ThsAdapter(transport)

    await adapter.hot_list(
        HotListRequest(), timeout_seconds=1.0, cancellation_token=None
    )
    await adapter.limit_up_pool(REASONS, timeout_seconds=1.0, cancellation_token=None)
    assert len(seen) == 2

    for _ in range(LANE_REST_AFTER_FAILURES):
        rest.hung_up("ths")
    for call in (
        adapter.hot_list(
            HotListRequest(), timeout_seconds=1.0, cancellation_token=None
        ),
        adapter.harden(REASONS, timeout_seconds=1.0, cancellation_token=None),
    ):
        with pytest.raises(UpstreamUnavailable, match="同花顺"):
            await call
    assert len(seen) == 2
