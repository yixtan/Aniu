"""Closed route registry, fallback policy, and response metadata assembly."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from backend.stock_api.public.budget import operation_deadline
from backend.stock_api.public.cancellation import (
    CancellationToken as AbortSignal,
)
from backend.stock_api.public.cancellation import (
    throw_if_cancelled as throw_if_aborted,
)
from backend.stock_api.public.contracts import (
    AnnouncementsRequest,
    ConnectMoneyFlowRequest,
    FinancialsRequest,
    ForecastRequest,
    IndexKlineRequest,
    IndexQuoteRequest,
    IndustryComparisonRequest,
    IntradayRequest,
    KlineRequest,
    MarketBreadthRequest,
    MarketReportsRequest,
    NewsFeedRequest,
    NewsSearchRequest,
    OperatingIndicatorsRequest,
    ProviderName,
    PublicStockRequest,
    QuoteSnapshotRequest,
    RatingsRequest,
    SectorMoneyFlowRequest,
    SectorRankingRequest,
    ShareholdersRequest,
    StockMoneyFlowHistoryRequest,
    StockMoneyFlowIntradayRequest,
    StockNewsRequest,
    StockRankingRequest,
    StockReportsRequest,
    ValuationRequest,
)
from backend.stock_api.public.errors import (
    InvalidStockRequest,
    NoStockData,
    PublicStockDataError,
    UnsupportedStockRequest,
    UpstreamUnavailable,
)
from backend.stock_api.public.normalizers.content import (
    normalize_announcements,
    normalize_financials,
    normalize_forecast,
    normalize_industry_comparison,
    normalize_market_reports,
    normalize_news_feed,
    normalize_news_search,
    normalize_operating_indicators,
    normalize_ratings,
    normalize_shareholders,
    normalize_stock_news,
    normalize_stock_reports,
    normalize_valuation,
)
from backend.stock_api.public.normalizers.market import (
    normalize_breadth,
    normalize_connect_flow,
    normalize_index_quotes,
    normalize_intraday,
    normalize_kline,
    normalize_quotes,
    normalize_ranking,
    normalize_sector_money_flow,
    normalize_stock_money_flow,
)
from backend.stock_api.public.normalizers.sina_money import (
    normalize_sina_money_history,
    normalize_sina_money_today,
    normalize_sina_sector_money,
    normalize_sina_stock_money_ranking,
)
from backend.stock_api.public.providers.sina import SINA_RANKING_MAX_DEPTH
from backend.stock_api.public.routing import (
    PublicProviderAdapters,
    RouteCandidate,
    route_candidate,
)
from backend.stock_api.public.signal_routes import signal_candidates

_candidate = route_candidate
_TOTAL_TIMEOUTS = {
    "market": 8.0,
    "quote": 8.0,
    "chart": 12.0,
    "ranking": 12.0,
    "money_flow": 12.0,
    "fundamentals": 15.0,
    "index": 12.0,
    "research": 25.0,
    "news": 12.0,
    # The per-stock 龙虎榜 makes up to three calls; the sentiment summary four
    # serial push2ex calls 1.5 s apart.
    "signals": 20.0,
    "sentiment": 30.0,
}
_UPSTREAM_REQUEST_TIMEOUTS = {
    "market": 3.0,
    "quote": 3.0,
    "chart": 4.0,
    "ranking": 4.0,
    "money_flow": 4.0,
    "fundamentals": 6.0,
    "index": 4.0,
    "research": 10.0,
    "news": 4.0,
    "signals": 6.0,
    "sentiment": 6.0,
}
_PROVIDER_LABELS = {
    "eastmoney": "东方财富",
    "tencent": "腾讯财经",
    "sina": "新浪财经",
    "ths": "同花顺",
}
_MINIMUM_CANDIDATE_SECONDS = 1.0
"""What a candidate still gets when its fallback's reserve would leave less."""


def _fallback_warning(
    previous_provider: ProviderName | None,
    provider: ProviderName,
    previous_empty: bool,
) -> str:
    status = "未返回有效数据" if previous_empty else "暂时不可用"
    label = _PROVIDER_LABELS[provider]
    if previous_provider == provider:
        # Two endpoints of one source, such as 同花顺's 涨停归因 then its
        # 涨停揭秘: naming the source twice would read 「同花顺暂时不可用，
        # 已使用同花顺」. The candidate or its normalizer names the endpoints.
        return f"{label}的首选接口{status}，已改用它的备用接口。"
    previous = (
        _PROVIDER_LABELS[previous_provider] if previous_provider else "首选数据源"
    )
    return f"{previous}{status}，已使用{label}。"


class PublicStockRouter:
    """Routes one business request to one complete source response."""

    def __init__(self, adapters: PublicProviderAdapters) -> None:
        self._adapters = adapters

    async def execute(
        self,
        request: PublicStockRequest,
        *,
        cancellation_token: AbortSignal | None = None,
        diagnostic_provider: ProviderName | None = None,
    ) -> dict[str, object]:
        throw_if_aborted(cancellation_token)
        candidates = self.candidates_for(request)
        if diagnostic_provider is not None:
            if diagnostic_provider not in _PROVIDER_LABELS:
                raise InvalidStockRequest("诊断数据源无效。")
            candidates = [
                candidate
                for candidate in candidates
                if candidate.provider == diagnostic_provider
            ][:1]
            if not candidates:
                raise UnsupportedStockRequest(
                    f"{request.operation} 不支持固定来源 {diagnostic_provider}。"
                )
        if not candidates:
            raise UnsupportedStockRequest(f"{request.operation} 没有可用数据源。")

        category = request.operation.split(".", maxsplit=1)[0]
        deadline = time.monotonic() + _TOTAL_TIMEOUTS[category]
        attempted: list[ProviderName] = []
        failures = 0
        last_error: PublicStockDataError | None = None
        previous_provider: ProviderName | None = None
        previous_empty = False

        for index, candidate in enumerate(candidates):
            throw_if_aborted(cancellation_token)
            if candidate.provider not in attempted:
                attempted.append(candidate.provider)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = UpstreamUnavailable("公开数据请求超过总超时。")
                break
            budget = remaining
            if candidate.reserve_seconds and index + 1 < len(candidates):
                # Keep the next source's share. Time this one spends queued
                # behind parallel calls at a one-at-a-time gate would otherwise
                # come out of the fallback's, which then never starts.
                budget = max(
                    remaining - candidate.reserve_seconds,
                    min(remaining, _MINIMUM_CANDIDATE_SECONDS),
                )
            request_timeout = min(_UPSTREAM_REQUEST_TIMEOUTS[category], budget)
            error: PublicStockDataError
            deadline_token = operation_deadline.set(time.monotonic() + budget)
            try:
                # Adapters may retry a primary URL with a fallback URL. Each HTTP
                # request gets its own timeout; only the operation total is shared.
                async with asyncio.timeout(budget):
                    raw = await candidate.execute(request_timeout, cancellation_token)
                normalized = candidate.normalize(raw)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                error = UpstreamUnavailable("公开数据请求超过总超时。")
            except (InvalidStockRequest, UnsupportedStockRequest):
                raise
            except (NoStockData, UpstreamUnavailable) as exc:
                error = exc
            except Exception as exc:  # adapters must not leak parser internals
                if cancellation_token is not None and cancellation_token.aborted:
                    cancellation_token.throw_if_aborted()
                error = UpstreamUnavailable("公开数据源返回了无法处理的数据。")
                error.__cause__ = exc
            else:
                fallback_used = failures > 0
                warnings: list[str] = []
                if fallback_used:
                    warnings.append(
                        _fallback_warning(
                            previous_provider, candidate.provider, previous_empty
                        )
                    )
                if candidate.warning:
                    warnings.append(candidate.warning)
                warnings.extend(normalized.warnings)
                meta: dict[str, object] = {
                    "operation": request.operation,
                    "source": candidate.provider,
                    "attempted_sources": attempted,
                    "fallback_used": fallback_used,
                    "degraded": candidate.degraded or normalized.degraded,
                    "fetched_at": _now_iso(),
                    "warnings": warnings,
                }
                if normalized.partial:
                    # Part of the answer failed and a second ask may bring it;
                    # the service keeps such a result only briefly.
                    meta["partial"] = True
                return {"data": normalized.data, "meta": meta}
            finally:
                operation_deadline.reset(deadline_token)

            last_error = error
            if isinstance(error, NoStockData) and candidate.fallback_on_empty:
                failures += 1
                previous_provider = candidate.provider
                previous_empty = True
                continue
            if not error.retryable:
                raise error
            failures += 1
            previous_provider = candidate.provider
            previous_empty = False
            if diagnostic_provider is not None:
                raise error

        if last_error is None:
            raise UpstreamUnavailable("公开数据源暂时不可用。")
        raise last_error

    def candidates_for(self, request: PublicStockRequest) -> list[RouteCandidate]:
        signals = signal_candidates(self._adapters, request)
        if signals is not None:
            return signals
        eastmoney = self._adapters.eastmoney
        tencent = self._adapters.tencent
        sina = self._adapters.sina
        if isinstance(request, MarketBreadthRequest):
            return [
                _candidate(
                    "eastmoney",
                    "em_market_breadth",
                    eastmoney.breadth,
                    request,
                    lambda raw: normalize_breadth("eastmoney", raw),
                )
            ]
        if isinstance(request, IndexQuoteRequest):
            return [
                _candidate(
                    "tencent",
                    "tx_index_quotes",
                    tencent.quote_snapshot,
                    request,
                    lambda raw: normalize_index_quotes("tencent", raw, request),
                    fallback_on_empty=True,
                ),
                _candidate(
                    "sina",
                    "sina_index_quotes",
                    sina.quote_snapshot,
                    request,
                    lambda raw: normalize_index_quotes("sina", raw, request),
                    fallback_on_empty=True,
                ),
                _candidate(
                    "eastmoney",
                    "em_index_quotes",
                    eastmoney.quote_snapshot,
                    request,
                    lambda raw: normalize_index_quotes("eastmoney", raw, request),
                    fallback_on_empty=True,
                ),
            ]
        if isinstance(request, IndexKlineRequest):
            index_candidates: list[RouteCandidate] = []
            if not request.period.endswith("m"):
                index_candidates.append(
                    _candidate(
                        "tencent",
                        "tx_index_history",
                        tencent.kline,
                        request,
                        lambda raw: normalize_kline("tencent", raw, request),
                        fallback_on_empty=True,
                    )
                )
            if (
                request.period != "1m"
                and request.start_date is None
                and request.end_date is None
            ):
                index_candidates.append(
                    _candidate(
                        "sina",
                        "sina_index_history",
                        sina.kline,
                        request,
                        lambda raw: normalize_kline("sina", raw, request),
                        fallback_on_empty=True,
                    )
                )
            index_candidates.append(
                _candidate(
                    "eastmoney",
                    "em_index_intraday"
                    if request.period == "1m"
                    and request.start_date is None
                    and request.end_date is None
                    else "em_index_history",
                    eastmoney.index_kline_1m
                    if request.period == "1m"
                    and request.start_date is None
                    and request.end_date is None
                    else eastmoney.kline,
                    request,
                    lambda raw: normalize_kline("eastmoney", raw, request),
                    fallback_on_empty=True,
                )
            )
            return index_candidates
        if isinstance(request, QuoteSnapshotRequest):
            return [
                _candidate(
                    "tencent",
                    "tx_realtime_quotes",
                    tencent.quote_snapshot,
                    request,
                    lambda raw: normalize_quotes("tencent", raw, request),
                    fallback_on_empty=True,
                ),
                _candidate(
                    "sina",
                    "sina_realtime_quotes",
                    sina.quote_snapshot,
                    request,
                    lambda raw: normalize_quotes("sina", raw, request),
                    fallback_on_empty=True,
                ),
                _candidate(
                    "eastmoney",
                    "em_realtime_quote"
                    if len(request.symbols) == 1 and request.detail == "full"
                    else "em_batch_quotes",
                    eastmoney.quote_snapshot,
                    request,
                    lambda raw: normalize_quotes("eastmoney", raw, request),
                    fallback_on_empty=True,
                ),
            ]
        if isinstance(request, KlineRequest):
            candidates: list[RouteCandidate] = []
            if request.period != "1m":
                candidates.append(
                    _candidate(
                        "tencent",
                        "tx_price_history",
                        tencent.kline,
                        request,
                        lambda raw: normalize_kline("tencent", raw, request),
                        fallback_on_empty=True,
                    )
                )
                if (
                    request.adjust == "none"
                    and request.start_date is None
                    and request.end_date is None
                ):
                    candidates.append(
                        _candidate(
                            "sina",
                            "sina_price_history",
                            sina.kline,
                            request,
                            lambda raw: normalize_kline("sina", raw, request),
                            fallback_on_empty=True,
                        )
                    )
            candidates.append(
                _candidate(
                    "eastmoney",
                    "em_price_history",
                    eastmoney.kline,
                    request,
                    lambda raw: normalize_kline("eastmoney", raw, request),
                    fallback_on_empty=True,
                )
            )
            return candidates
        if isinstance(request, IntradayRequest):
            candidates = []
            if request.days == 1:
                candidates.append(
                    _candidate(
                        "tencent",
                        "tx_intraday_trends",
                        tencent.intraday,
                        request,
                        lambda raw: normalize_intraday("tencent", raw, request),
                        fallback_on_empty=True,
                    )
                )
            candidates.append(
                _candidate(
                    "eastmoney",
                    "em_intraday_trends",
                    eastmoney.intraday,
                    request,
                    lambda raw: normalize_intraday("eastmoney", raw, request),
                    fallback_on_empty=True,
                )
            )
            return candidates
        if isinstance(request, StockRankingRequest):
            candidates = []
            if (
                request.market == "all_a"
                and request.sort == "net_inflow"
                and request.page * request.limit <= SINA_RANKING_MAX_DEPTH
            ):
                # Sina first even though East Money's 主力 is the richer figure:
                # push2 dropped half of these from 2026-09-23, and one basis
                # all day beats two that trade places call by call.
                candidates.append(
                    _candidate(
                        "sina",
                        "sina_stock_money_flow_ranking",
                        sina.stock_money_flow_ranking,
                        request,
                        lambda raw: normalize_sina_stock_money_ranking(raw, request),
                        fallback_on_empty=True,
                    )
                )
            if request.market == "all_a" and request.sort in {
                "price",
                "volume",
                "amount",
            }:
                candidates.append(
                    _candidate(
                        "tencent",
                        "tx_stock_rank",
                        tencent.stock_ranking,
                        request,
                        lambda raw: normalize_ranking(
                            "tencent", raw, request, sectors=False
                        ),
                        fallback_on_empty=True,
                    )
                )
            if request.sort in {"price", "change_percent", "volume", "amount"}:
                candidates.append(
                    _candidate(
                        "sina",
                        "sina_market_snapshot",
                        sina.stock_ranking,
                        request,
                        lambda raw: normalize_ranking(
                            "sina", raw, request, sectors=False
                        ),
                        fallback_on_empty=True,
                    )
                )
            candidates.append(
                _candidate(
                    "eastmoney",
                    "em_market_snapshot",
                    eastmoney.stock_ranking,
                    request,
                    lambda raw: normalize_ranking(
                        "eastmoney", raw, request, sectors=False
                    ),
                    fallback_on_empty=True,
                )
            )
            return candidates
        if isinstance(request, SectorRankingRequest):
            candidates = []
            if request.sort == "price":
                candidates.append(
                    _candidate(
                        "tencent",
                        "tx_sector_rank",
                        tencent.sector_ranking,
                        request,
                        lambda raw: normalize_ranking(
                            "tencent", raw, request, sectors=True
                        ),
                        fallback_on_empty=True,
                    )
                )
            if request.sector_type == "industry" and request.sort in {
                "price",
                "change_percent",
                "volume",
                "amount",
            }:
                candidates.append(
                    _candidate(
                        "sina",
                        "sina_industry_overview",
                        sina.sector_ranking,
                        request,
                        lambda raw: normalize_ranking(
                            "sina", raw, request, sectors=True, paginate_locally=True
                        ),
                        fallback_on_empty=True,
                    )
                )
            if request.sort == "net_inflow" or (
                request.sector_type == "concept" and request.sort == "change_percent"
            ):
                candidates.append(
                    _candidate(
                        "sina",
                        "sina_sector_money_flow",
                        sina.sector_money_flow,
                        request,
                        lambda raw: normalize_sina_sector_money(raw, request),
                        fallback_on_empty=True,
                    )
                )
            candidates.append(
                _candidate(
                    "eastmoney",
                    "em_market_snapshot",
                    eastmoney.sector_ranking,
                    request,
                    lambda raw: normalize_ranking(
                        "eastmoney", raw, request, sectors=True
                    ),
                    fallback_on_empty=True,
                )
            )
            return candidates
        if isinstance(request, StockMoneyFlowHistoryRequest):
            return [
                _candidate(
                    "sina",
                    "sina_stock_money_flow",
                    sina.stock_money_flow_history,
                    request,
                    lambda raw: normalize_sina_money_history(raw, request),
                    fallback_on_empty=True,
                ),
                _candidate(
                    "eastmoney",
                    "em_money_flow_history",
                    eastmoney.stock_money_flow_history,
                    request,
                    lambda raw: normalize_stock_money_flow(raw, request),
                    fallback_on_empty=True,
                ),
            ]
        if isinstance(request, StockMoneyFlowIntradayRequest):
            # East Money first: only it has the minute series. Sina's day total
            # still answers "in or out so far today" when push2 will not.
            return [
                _candidate(
                    "eastmoney",
                    "em_money_flow_intraday",
                    eastmoney.stock_money_flow_intraday,
                    request,
                    lambda raw: normalize_stock_money_flow(raw, request),
                    fallback_on_empty=True,
                ),
                _candidate(
                    "sina",
                    "sina_stock_money_flow_today",
                    sina.stock_money_flow_today,
                    request,
                    lambda raw: normalize_sina_money_today(raw, request),
                ),
            ]
        if isinstance(request, SectorMoneyFlowRequest):
            return [
                _candidate(
                    "sina",
                    "sina_sector_money_flow",
                    sina.sector_money_flow,
                    request,
                    lambda raw: normalize_sina_sector_money(raw, request),
                    fallback_on_empty=True,
                ),
                _candidate(
                    "eastmoney",
                    "em_market_snapshot",
                    eastmoney.sector_money_flow,
                    request,
                    lambda raw: normalize_sector_money_flow(raw, request),
                ),
            ]
        if isinstance(request, ConnectMoneyFlowRequest):
            return [
                _candidate(
                    "eastmoney",
                    "em_connect_flow",
                    eastmoney.connect_money_flow,
                    request,
                    lambda raw: normalize_connect_flow(raw, request),
                )
            ]
        if isinstance(request, FinancialsRequest):
            return [
                _candidate(
                    "eastmoney",
                    "em_financial_summary"
                    if request.mode == "latest"
                    else "em_quarterly_financials",
                    eastmoney.financials,
                    request,
                    lambda raw: normalize_financials(raw, request),
                )
            ]
        if isinstance(request, ShareholdersRequest):
            return [
                _candidate(
                    "eastmoney",
                    "em_shareholder_counts",
                    eastmoney.shareholders,
                    request,
                    lambda raw: normalize_shareholders(raw, request),
                )
            ]
        if isinstance(request, ValuationRequest):
            return [
                _candidate(
                    "eastmoney",
                    "em_valuation_percentiles",
                    eastmoney.valuation,
                    request,
                    lambda raw: normalize_valuation(raw, request),
                )
            ]
        if isinstance(request, IndustryComparisonRequest):
            return [
                _candidate(
                    "eastmoney",
                    "em_industry_comparison",
                    eastmoney.industry_comparison,
                    request,
                    lambda raw: normalize_industry_comparison(raw, request),
                )
            ]
        if isinstance(request, OperatingIndicatorsRequest):
            return [
                _candidate(
                    "eastmoney",
                    "em_supplementary_indicators",
                    eastmoney.operating_indicators,
                    request,
                    lambda raw: normalize_operating_indicators(raw, request),
                )
            ]
        if isinstance(request, MarketReportsRequest):
            return [
                _candidate(
                    "eastmoney",
                    f"em_{request.category}_reports",
                    eastmoney.market_reports,
                    request,
                    lambda raw: normalize_market_reports(raw, request),
                )
            ]
        if isinstance(request, StockReportsRequest):
            return [
                _candidate(
                    "eastmoney",
                    "em_stock_reports",
                    eastmoney.stock_reports,
                    request,
                    lambda raw: normalize_stock_reports(raw, request),
                )
            ]
        if isinstance(request, ForecastRequest):
            return [
                _candidate(
                    "eastmoney",
                    "em_profit_forecast"
                    if request.mode == "summary"
                    else "em_institutional_forecasts",
                    eastmoney.forecast,
                    request,
                    lambda raw: normalize_forecast(raw, request),
                )
            ]
        if isinstance(request, RatingsRequest):
            return [
                _candidate(
                    "eastmoney",
                    "em_rating_statistics",
                    eastmoney.ratings,
                    request,
                    lambda raw: normalize_ratings(raw, request),
                )
            ]
        if isinstance(request, NewsFeedRequest):
            if request.feed in {"headlines", "flash"}:
                return [
                    _candidate(
                        "eastmoney",
                        "em_top_news" if request.feed == "headlines" else "em_kuaixun",
                        eastmoney.feed,
                        request,
                        lambda raw: normalize_news_feed("eastmoney", raw, request),
                    ),
                    _candidate(
                        "sina",
                        "sina_rolling_news",
                        sina.feed,
                        request,
                        lambda raw: normalize_news_feed("sina", raw, request),
                    ),
                ]
            if request.feed == "finance":
                return [
                    _candidate(
                        "eastmoney",
                        "em_channel_news",
                        eastmoney.feed,
                        request,
                        lambda raw: normalize_news_feed("eastmoney", raw, request),
                    ),
                    _candidate(
                        "sina",
                        "sina_rolling_news",
                        sina.feed,
                        request,
                        lambda raw: normalize_news_feed("sina", raw, request),
                    ),
                ]
            if request.feed == "global":
                return [
                    _candidate(
                        "eastmoney",
                        "em_channel_news",
                        eastmoney.feed,
                        request,
                        lambda raw: normalize_news_feed("eastmoney", raw, request),
                    )
                ]
            return [
                _candidate(
                    "sina",
                    "sina_rolling_news",
                    sina.feed,
                    request,
                    lambda raw: normalize_news_feed("sina", raw, request),
                )
            ]
        if isinstance(request, StockNewsRequest):
            return [
                _candidate(
                    "tencent",
                    "tx_stock_news",
                    tencent.stock_news,
                    request,
                    lambda raw: normalize_stock_news("tencent", raw, request),
                ),
                _candidate(
                    "sina",
                    "sina_stock_news_page",
                    sina.stock_news,
                    request,
                    lambda raw: normalize_stock_news("sina", raw, request),
                ),
            ]
        if isinstance(request, AnnouncementsRequest):
            return [
                _candidate(
                    "eastmoney",
                    "em_stock_announcements",
                    eastmoney.announcements,
                    request,
                    lambda raw: normalize_announcements(raw, request),
                )
            ]
        if isinstance(request, NewsSearchRequest):
            return [
                _candidate(
                    "eastmoney",
                    "em_global_search",
                    eastmoney.news_search,
                    request,
                    lambda raw: normalize_news_search(raw, request),
                )
            ]
        raise InvalidStockRequest("不支持的公开股票数据请求。")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = ["PublicProviderAdapters", "PublicStockRouter", "RouteCandidate"]
