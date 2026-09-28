"""The datacenter gateway says "no rows" as a failure; only that one is empty.

At 02:31 on 2026-09-29 the gateway answered 600519's 龙虎榜 for a week it was
not listed with ``success:false, code 9201, 返回数据为空`` — the fixture is
that answer verbatim. Read the way the F10 calls read it, every stock not on
the list would reach the model as an outage.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.stock_api.public.errors import UpstreamUnavailable
from backend.stock_api.public.http import PublicHttpTransport
from backend.stock_api.public.providers.eastmoney import EastMoneyAdapter
from backend.stock_api.public.providers.eastmoney_datacenter import (
    datacenter_page,
    datacenter_rows,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> object:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def adapter_answering(
    payload: object,
) -> tuple[EastMoneyAdapter, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    transport = PublicHttpTransport(transport=httpx.MockTransport(handler))
    transport._gates["eastmoney_f10"].minimum_start_interval = 0
    return EastMoneyAdapter(transport), seen


async def rows(adapter: EastMoneyAdapter) -> list[dict[str, object]]:
    return await adapter._datacenter_rows(
        operation="signals.dragon_tiger_stock",
        endpoint="em_dragon_tiger_records",
        report="RPT_DAILYBILLBOARD_DETAILSNEW",
        filters=(
            "TRADE_DATE>='2026-09-21'",
            "(TRADE_DATE<='2026-09-28')",
            'SECURITY_CODE="600519"',
        ),
        sort_columns=("TRADE_DATE", "SECURITY_CODE"),
        sort_types=(-1, 1),
        page_size=50,
        timeout_seconds=1.0,
        cancellation_token=None,
    )


@pytest.mark.asyncio
async def test_no_matching_rows_is_an_empty_list_not_an_outage() -> None:
    adapter, seen = adapter_answering(fixture("datacenter_empty.json"))

    assert await rows(adapter) == []

    params = seen[0].url.params
    assert seen[0].url.host == "datacenter.eastmoney.com"
    assert seen[0].url.path == "/securities/api/data/v1/get"
    assert params["reportName"] == "RPT_DAILYBILLBOARD_DETAILSNEW"
    assert params["filter"] == (
        "(TRADE_DATE>='2026-09-21')(TRADE_DATE<='2026-09-28')(SECURITY_CODE=\"600519\")"
    )
    assert (params["sortColumns"], params["sortTypes"]) == (
        "TRADE_DATE,SECURITY_CODE",
        "-1,1",
    )
    assert (params["source"], params["client"], params["columns"]) == (
        "WEB",
        "WEB",
        "ALL",
    )
    assert (params["pageNumber"], params["pageSize"]) == ("1", "50")
    assert seen[0].headers["Referer"] == "https://data.eastmoney.com/"


@pytest.mark.asyncio
async def test_any_other_refusal_is_retryable_and_says_what_the_gateway_said() -> None:
    # Constructed: the research saw code 9501 for mismatched sort parameters
    # but did not keep its text, so the message here is a stand-in.
    adapter, _ = adapter_answering(
        {
            "version": None,
            "result": None,
            "success": False,
            "message": "请求参数错误",
            "code": 9501,
        }
    )

    with pytest.raises(UpstreamUnavailable) as caught:
        await rows(adapter)

    assert caught.value.retryable is True
    assert caught.value.error_category == "business_failure"
    assert "请求参数错误" in str(caught.value)
    assert "9501" in str(caught.value)


@pytest.mark.asyncio
async def test_success_without_a_row_list_is_not_mistaken_for_empty() -> None:
    adapter, _ = adapter_answering(
        {"success": True, "code": 0, "message": "ok", "result": None}
    )

    with pytest.raises(UpstreamUnavailable, match="缺少数据列表"):
        await rows(adapter)


@pytest.mark.asyncio
async def test_rows_come_back_as_dicts_in_the_gateway_order() -> None:
    adapter, _ = adapter_answering(
        {
            "success": True,
            "code": 0,
            "message": "ok",
            "result": {
                "pages": 1,
                "count": 2,
                "data": [
                    {"TRADE_DATE": "2026-08-25 00:00:00", "TRADE_ID": 100401673},
                    "not a row",
                    {"TRADE_DATE": "2026-08-05 00:00:00", "TRADE_ID": 100390054},
                ],
            },
        }
    )

    assert [row["TRADE_ID"] for row in await rows(adapter)] == [100401673, 100390054]


@pytest.mark.asyncio
async def test_misuse_fails_before_a_request_is_sent() -> None:
    adapter, seen = adapter_answering(fixture("datacenter_empty.json"))

    with pytest.raises(ValueError, match="pair up"):
        await adapter._datacenter_rows(
            operation="signals.margin",
            endpoint="em_margin_detail",
            report="RPTA_WEB_RZRQ_GGMX",
            filters=('SCODE="600487"',),
            sort_columns=("DATE",),
            sort_types=(-1, 1),
            page_size=10,
            timeout_seconds=1.0,
            cancellation_token=None,
        )
    with pytest.raises(TypeError, match="sequence of clauses"):
        await adapter._datacenter_rows(
            operation="signals.margin",
            endpoint="em_margin_detail",
            report="RPTA_WEB_RZRQ_GGMX",
            filters='(SCODE="600487")',
            sort_columns=("DATE",),
            sort_types=(-1,),
            page_size=10,
            timeout_seconds=1.0,
            cancellation_token=None,
        )
    assert seen == []


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # The 2026-09-28 list: 14 rows kept of the 67, and the count with them.
        (fixture("em_lhb_market_20260928.json"), 14),
        ({"success": True, "code": 0, "result": {"data": [], "count": "7"}}, None),
        (fixture("datacenter_empty.json"), None),
    ],
    ids=["count", "count_not_a_number", "empty_match"],
)
def test_a_page_carries_the_gateways_count_of_every_match(
    payload: object, expected: int | None
) -> None:
    rows, count = datacenter_page(payload, "RPT_DAILYBILLBOARD_DETAILSNEW")

    assert count == expected
    assert rows == datacenter_rows(payload, "RPT_DAILYBILLBOARD_DETAILSNEW")
