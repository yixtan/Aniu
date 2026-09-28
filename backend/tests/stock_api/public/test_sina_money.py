"""Per-stock money-flow history, from Sina first and East Money after.

Until 2026-09-29 the Sina half answered a different question from the one
asked: the biggest-inflow days on record rather than the latest, with the
all-order net labelled 主力 and fractions read as percents. The fixture is a
real response from the evening of 2026-09-28.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import httpx
import pytest

from backend.stock_api.public.contracts import (
    StockMoneyFlowHistoryRequest,
)
from backend.stock_api.public.errors import NoStockData
from backend.stock_api.public.http import PublicHttpTransport
from backend.stock_api.public.normalizers.market import normalize_stock_money_flow
from backend.stock_api.public.normalizers.sina_money import (
    HISTORY_BASIS,
    normalize_sina_money_history,
)
from backend.stock_api.public.providers.sina import SinaAdapter

_FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> object:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def items_of(data: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], data["items"])


def sina_transport(payload: object) -> tuple[SinaAdapter, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    transport = PublicHttpTransport(transport=httpx.MockTransport(handler))
    transport._gates["sina"].minimum_start_interval = 0
    return SinaAdapter(transport), seen


def test_history_reads_sinas_main_class_not_its_all_order_total() -> None:
    """600487 closed limit-down on 09-28. The old parser showed that day as
    −0.1% and reported the all-order net (−17.5 亿) as 主力 (−12.1 亿)."""

    result = normalize_sina_money_history(
        fixture("sina_money_history.json"),
        StockMoneyFlowHistoryRequest("600487.SH", limit=3),
    )

    items = items_of(result.data)
    assert [item["time"] for item in items] == [
        "2026-09-23",
        "2026-09-24",
        "2026-09-28",
    ]
    latest = items[-1]
    assert latest["main_net_inflow"] == pytest.approx(-1_210_075_931.03)
    assert latest["net_inflow"] == pytest.approx(-1_745_029_883.03)
    assert latest["change_percent"] == pytest.approx(-10.003)
    assert latest["turnover_rate"] == pytest.approx(4.699, abs=1e-3)
    assert result.warnings == (HISTORY_BASIS,)
    assert result.degraded is True


@pytest.mark.asyncio
async def test_history_asks_sina_for_the_latest_days_not_the_biggest() -> None:
    """sort=netamount returned 600487's five biggest inflow days on record,
    from April to August, to a caller asking for the last five."""

    sina, seen = sina_transport(fixture("sina_money_history.json"))

    await sina.stock_money_flow_history(
        StockMoneyFlowHistoryRequest("600487.SH", page=2, limit=5),
        timeout_seconds=2,
        cancellation_token=None,
    )

    params = seen[0].url.params
    assert (params["sort"], params["asc"]) == ("opendate", "0")
    assert (params["page"], params["num"], params["daima"]) == ("2", "5", "sh600487")


def test_eastmoney_history_page_two_holds_the_older_days() -> None:
    """East Money returns the last page × limit days oldest first."""

    four_days = {
        "data": {"klines": [f"2026-09-{day},{day}" for day in (21, 22, 23, 24)]}
    }
    two_days = {"data": {"klines": ["2026-09-23,23", "2026-09-24,24"]}}

    def times(raw: object, page: int) -> list[object]:
        request = StockMoneyFlowHistoryRequest("600487.SH", page=page, limit=2)
        return [
            item["time"]
            for item in items_of(normalize_stock_money_flow(raw, request).data)
        ]

    assert times(two_days, 1) == ["2026-09-23", "2026-09-24"]
    assert times(four_days, 2) == ["2026-09-21", "2026-09-22"]
    with pytest.raises(NoStockData):
        times(four_days, 3)
