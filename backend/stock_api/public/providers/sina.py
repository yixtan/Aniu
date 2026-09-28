"""Sina Finance adapter for the fixed public A-share catalog."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import cast

from backend.stock_api.public.cancellation import CancellationToken as AbortSignal
from backend.stock_api.public.contracts import (
    IndexKlineRequest,
    IndexQuoteRequest,
    KlineRequest,
    NewsFeedRequest,
    QuoteSnapshotRequest,
    SectorMoneyFlowRequest,
    SectorRankingRequest,
    StockMoneyFlowHistoryRequest,
    StockMoneyFlowIntradayRequest,
    StockNewsRequest,
    StockRankingRequest,
    market_symbol_code,
    market_symbol_market,
)
from backend.stock_api.public.errors import UpstreamUnavailable
from backend.stock_api.public.providers.base import (
    FixedPublicAdapter,
    build_url,
    parse_jsonp,
    public_headers,
)


class SinaAdapter(FixedPublicAdapter):
    provider = "sina"

    async def quote_snapshot(
        self,
        request: QuoteSnapshotRequest | IndexQuoteRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        symbols = ",".join(_sina_symbol(item) for item in request.symbols)
        source = await self._text(
            operation=request.operation,
            endpoint="sina_realtime_quotes",
            url=f"https://hq.sinajs.cn/list={symbols}",
            parameters={"symbols": symbols},
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
            headers=public_headers(referer="https://finance.sina.com.cn/"),
            encoding="gbk",
        )
        return {"quotes": _parse_quotes(source)}

    async def kline(
        self,
        request: KlineRequest | IndexKlineRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        scale = {
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "60m": 60,
            "day": 240,
            "week": 1200,
            "month": 7200,
        }[request.period]
        endpoint = (
            "sina_intraday_kline"
            if request.period.endswith("m")
            else "sina_price_history"
        )
        origin = (
            "https://money.finance.sina.com.cn"
            if request.period.endswith("m")
            else "https://quotes.sina.cn"
        )
        path = (
            "/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
            if request.period.endswith("m")
            else "/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
        )
        params = {
            "symbol": _sina_symbol(request.symbol),
            "scale": scale,
            "ma": "no",
            "datalen": request.limit,
        }
        return await self._json(
            operation=request.operation,
            endpoint=endpoint,
            url=build_url(origin, path, params),
            parameters=params,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
            headers=public_headers(referer="https://finance.sina.com.cn/"),
        )

    async def stock_ranking(
        self,
        request: StockRankingRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        sort = {
            "price": "trade",
            "change_percent": "changepercent",
            "volume": "volume",
            "amount": "amount",
        }.get(request.sort)
        if sort is None:
            raise UpstreamUnavailable("新浪不支持该个股排行指标。", retryable=False)
        params = {
            "page": request.page,
            "num": request.limit,
            "sort": sort,
            "asc": 1 if request.order == "asc" else 0,
            "node": {
                "all_a": "hs_a",
                "sh_a": "sh_a",
                "sz_a": "sz_a",
                "chinext": "cyb",
                "star": "kcb",
            }[request.market],
            "symbol": "",
            "_s_r_a": "init",
        }
        return await self._json(
            operation=request.operation,
            endpoint="sina_market_snapshot",
            url=build_url(
                "https://vip.stock.finance.sina.com.cn",
                "/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
                params,
            ),
            parameters=params,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
            headers=public_headers(referer="https://finance.sina.com.cn/"),
        )

    async def sector_ranking(
        self,
        request: SectorRankingRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        source = await self._text(
            operation=request.operation,
            endpoint="sina_industry_overview",
            url=build_url(
                "https://money.finance.sina.com.cn",
                "/q/view/newFLJK.php",
                {"param": "industry"},
            ),
            parameters={},
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
            headers=public_headers(referer="https://finance.sina.com.cn/"),
            encoding="gbk",
            parser=parse_jsonp,
        )
        return source

    async def stock_money_flow_history(
        self,
        request: StockMoneyFlowHistoryRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        # Newest day first. It used to be sort=netamount, which answered "the
        # N days with the biggest inflow on record" — for 600487 on 09-28 that
        # was May, June and August, while the caller asked for the last N days.
        params = {
            "page": request.page,
            "num": request.limit,
            "sort": "opendate",
            "asc": 0,
            "daima": _sina_symbol(request.symbol),
        }
        return await self._money_flow(
            request.operation,
            "sina_stock_money_flow",
            "MoneyFlow.ssl_qsfx_zjlrqs",
            params,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )

    async def stock_money_flow_today(
        self,
        request: StockMoneyFlowIntradayRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        """Today's totals by order class. Sina has no minute series."""

        params = {"daima": _sina_symbol(request.symbol)}
        return await self._money_flow(
            request.operation,
            "sina_stock_money_flow_today",
            "MoneyFlow.ssi_ssfx_flzjtj",
            params,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )

    async def stock_money_flow_ranking(
        self,
        request: StockRankingRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        """All A-shares ranked by Sina's 主力 (r0) net inflow.

        Sina's list mixes in ETFs, a sixth of the top 60 on 2026-09-28, and no
        parameter filters them out. So this fetches the first page deep enough
        to survive dropping them, and the normalizer cuts the requested page.
        """

        if request.sort != "net_inflow" or request.market != "all_a":
            raise UpstreamUnavailable(
                "新浪资金流排行只支持全部 A 股按净流入排序。", retryable=False
            )
        params = {
            "page": 1,
            "num": sina_ranking_rows(request),
            "sort": "r0_net",
            "asc": 1 if request.order == "asc" else 0,
            "bankuai": "",
            "shichang": "",
        }
        return await self._money_flow(
            request.operation,
            "sina_stock_money_flow_ranking",
            "MoneyFlow.ssl_bkzj_ssggzj",
            params,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )

    async def sector_money_flow(
        self,
        request: SectorRankingRequest | SectorMoneyFlowRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        """Sina's own industry or concept boards, with each board's net inflow."""

        if isinstance(request, SectorMoneyFlowRequest):
            sort, order = "netamount", "desc"
        else:
            sort = {"net_inflow": "netamount", "change_percent": "avg_changeratio"}.get(
                request.sort, ""
            )
            order = request.order
            if not sort:
                raise UpstreamUnavailable(
                    "新浪板块资金流不支持该排序指标。", retryable=False
                )
        params = {
            "page": request.page,
            "num": request.limit,
            "sort": sort,
            "asc": 1 if order == "asc" else 0,
            "fenlei": {"industry": 0, "concept": 1}[request.sector_type],
        }
        return await self._money_flow(
            request.operation,
            "sina_sector_money_flow",
            "MoneyFlow.ssl_bkzj_bk",
            params,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )

    async def _money_flow(
        self,
        operation: str,
        endpoint: str,
        method: str,
        params: Mapping[str, object],
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        return await self._json(
            operation=operation,
            endpoint=endpoint,
            url=build_url(
                "https://vip.stock.finance.sina.com.cn",
                f"/quotes_service/api/json_v2.php/{method}",
                params,
            ),
            parameters=params,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
            headers=public_headers(referer="https://finance.sina.com.cn/"),
        )

    async def feed(
        self,
        request: NewsFeedRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        channel = (
            "finance"
            if request.feed in {"headlines", "finance"}
            else "stocks"
            if request.feed in {"flash", "stocks"}
            else "money"
        )
        params = {
            "pageid": 153,
            "lid": {"finance": 2516, "stocks": 2517, "money": 2519}[channel],
            "num": request.limit,
            "page": request.page,
        }
        return await self._json(
            operation=request.operation,
            endpoint="sina_rolling_news",
            url=build_url("https://feed.mix.sina.com.cn", "/api/roll/get", params),
            parameters=params,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
            headers=public_headers(referer="https://finance.sina.com.cn/"),
        )

    async def stock_news(
        self,
        request: StockNewsRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> object:
        symbol = _sina_symbol(request.symbol)
        return {
            "body": await self._text(
                operation=request.operation,
                endpoint="sina_stock_news_page",
                url=f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{symbol}.phtml",
                parameters={"symbol": symbol},
                timeout_seconds=timeout_seconds,
                cancellation_token=cancellation_token,
                headers=public_headers(
                    referer="https://finance.sina.com.cn/", html=True
                ),
                encoding="gbk",
            )
        }


SINA_RANKING_MAX_DEPTH = 100
"""Deepest page × limit the Sina net-inflow ranking serves; deeper goes to
East Money, since over-fetching past this grows without bound."""


def sina_ranking_rows(request: StockRankingRequest) -> int:
    """Rows to ask Sina for so the page still fills after ETFs are dropped."""

    return min(request.page * request.limit * 2 + 20, 2 * SINA_RANKING_MAX_DEPTH + 20)


def _sina_symbol(symbol: str) -> str:
    return f"{market_symbol_market(symbol).lower()}{market_symbol_code(symbol)}"


def _parse_quotes(source: str) -> list[dict[str, object]]:
    quotes: list[dict[str, object]] = []
    pattern = re.compile(
        r'(?:^|[\r\n])\s*var\s+hq_str_([A-Za-z0-9_]+)="((?:\\.|[^"])*)";?'
    )
    for match in pattern.finditer(source):
        encoded = match.group(2)
        try:
            fields = cast(str, json.loads(f'"{encoded}"')).split(",")
        except (TypeError, ValueError, json.JSONDecodeError):
            fields = encoded.split(",")
        quotes.append(
            {
                "symbol": match.group(1),
                "name": _field(fields, 0),
                "open": _number_field(fields, 1),
                "previous_close": _number_field(fields, 2),
                "price": _number_field(fields, 3),
                "high": _number_field(fields, 4),
                "low": _number_field(fields, 5),
                "volume_shares": _number_field(fields, 8),
                "amount_cny": _number_field(fields, 9),
                "date": _field(fields, 30),
                "time": _field(fields, 31),
            }
        )
    return quotes


def _field(values: list[str], index: int) -> str:
    return values[index] if index < len(values) else ""


def _number_field(values: list[str], index: int) -> float | None:
    try:
        return float(_field(values, index))
    except ValueError:
        return None


__all__ = ["SINA_RANKING_MAX_DEPTH", "SinaAdapter", "sina_ranking_rows"]
