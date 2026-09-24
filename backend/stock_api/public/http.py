"""Rate-limited, observable transport used only by fixed public adapters."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TypeVar

import httpx

from backend.business.shared.stock_api_source import (
    current_stock_api_source,
    normalize_public_stock_operation_id,
)
from backend.stock_api.models import (
    StockApiCall,
    StockApiCallLogger,
    StockApiErrorCategory,
    emit_stock_api_call_log,
)
from backend.stock_api.public.cancellation import (
    CancellationToken as AbortSignal,
)
from backend.stock_api.public.cancellation import (
    await_with_cancellation as await_with_abort,
)
from backend.stock_api.public.cancellation import (
    throw_if_cancelled as throw_if_aborted,
)
from backend.stock_api.public.contracts import ProviderName
from backend.stock_api.public.errors import UpstreamUnavailable

_T = TypeVar("_T")
_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _public_log_error_message(error: UpstreamUnavailable) -> str:
    message = str(error)
    if "超时" in message:
        return "公开数据源请求超时。"
    if "无法解析" in message or "必填字段" in message:
        return "公开数据源响应无效。"
    return "公开数据源请求失败。"


_NETWORK_FAILURE_LABELS = {
    "RemoteProtocolError": "服务器没有回应就断开了连接",
    "ConnectError": "连不上服务器",
    "ReadError": "读取响应时连接断了",
    "WriteError": "发送请求时连接断了",
    "ProxyError": "本机代理出错",
}


def _network_failure_message(error: BaseException) -> str:
    """Say which network failure it was.

    Every one of these used to read 「公开数据源网络请求失败」. On 2026-09-24
    that cost an hour: push2.eastmoney.com had been hanging up on most
    requests for two days, and nothing recorded could tell a server that
    answers with a closed connection from one that cannot be reached, though
    the two call for different responses. The class name only, never
    ``str(error)``: an httpx message can carry the request URL.
    """

    name = type(error).__name__
    label = _NETWORK_FAILURE_LABELS.get(name)
    if label is None:
        return f"公开数据源网络请求失败（{name}）。"
    return f"公开数据源网络请求失败：{label}（{name}）。"


_LANE_LABELS = {
    "eastmoney": "东方财富",
    "eastmoney_f10": "东方财富 F10",
    "eastmoney_push": "东方财富盘中接口（push2）",
    "tencent": "腾讯财经",
    "sina": "新浪财经",
}

LANE_REST_AFTER_FAILURES = 4
"""Network failures in a row on one lane before it is rested.

Four rather than three because `PublicHttpTransport.request` already sends a
single-URL request twice, so this is two whole calls that got nowhere, not
one unlucky one.
"""

LANE_REST_SECONDS = 300.0
"""How long a lane is left alone once it has hung up that many times.

From 2026-09-23 push2 hung up on most requests from this machine, and the
runs went on asking — fifty failed calls in two days, each sent twice. On the
24th a quick burst of diagnostic requests was followed by push2his refusing
too. A host that is turning us away is not helped by being asked again, and
the asking may be part of why it keeps doing so. Five minutes is short enough
that a recovered host is back within one analysis slot.
"""


@dataclass(slots=True)
class LaneRest:
    """Stop asking a host that keeps hanging up, for a while.

    Only a failure with no response at all counts. A host that answers — with
    data, an error status, anything — is not hanging up, and clears the count.
    A timeout does neither: it may be a filter dropping us silently or only a
    slow network, and it is not this rule's place to guess which.

    After a rest the count stays one short of the limit, so the first request
    back is a probe: one more hang-up rests the lane again, one answer clears it.
    """

    threshold: int = LANE_REST_AFTER_FAILURES
    seconds: float = LANE_REST_SECONDS
    clock: Callable[[], float] = time.monotonic
    _failures: dict[str, int] = field(default_factory=dict, init=False)
    _resting_until: dict[str, float] = field(default_factory=dict, init=False)

    def remaining(self, lane: str) -> float:
        return max(0.0, self._resting_until.get(lane, 0.0) - self.clock())

    def hung_up(self, lane: str) -> None:
        count = self._failures.get(lane, 0) + 1
        if count >= self.threshold:
            self._resting_until[lane] = self.clock() + self.seconds
            count = self.threshold - 1
        self._failures[lane] = count

    def answered(self, lane: str) -> None:
        self._failures[lane] = 0

    def refusal(self, lane: str) -> UpstreamUnavailable:
        minutes = max(1, round(self.remaining(lane) / 60))
        label = _LANE_LABELS.get(lane, lane)
        # Retryable on purpose: the router may still have another provider to
        # try, and a rested lane must not cost the caller that fallback.
        return UpstreamUnavailable(
            f"{label}刚才连续多次没有回应就断开连接，本机暂停向它发请求约 "
            f"{minutes} 分钟；这一次没有发出请求。",
            error_category="network",
        )


@dataclass(frozen=True, slots=True)
class PublicHttpRequest:
    provider: ProviderName
    operation: str
    endpoint: str
    url: str
    parameters: Mapping[str, object]
    headers: Mapping[str, str]
    encoding: str = "utf-8"
    lane: str | None = None
    fallback_urls: tuple[str, ...] = ()
    method: str = "GET"
    body: bytes | str | None = None


@dataclass(slots=True)
class ProviderRequestGate:
    maximum_concurrency: int
    minimum_start_interval: float
    _semaphore: asyncio.Semaphore = field(init=False)
    _schedule_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _next_start: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.maximum_concurrency)

    async def run(
        self,
        operation: Callable[[], Awaitable[_T]],
        cancellation_token: AbortSignal | None,
    ) -> _T:
        await await_with_abort(self._semaphore.acquire(), cancellation_token)
        try:
            async with self._schedule_lock:
                now = time.monotonic()
                scheduled = max(now, self._next_start)
                self._next_start = scheduled + self.minimum_start_interval
            delay = scheduled - time.monotonic()
            if delay > 0:
                await await_with_abort(asyncio.sleep(delay), cancellation_token)
            return await operation()
        finally:
            self._semaphore.release()


class PublicHttpTransport:
    """HTTP boundary with per-provider gates and one same-source retry."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        call_logger: StockApiCallLogger | None = None,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        lane_rest: LaneRest | None = None,
    ) -> None:
        self._client = http_client
        self._transport = transport
        self._owns_client = http_client is None
        self._call_logger = call_logger
        self._max_response_bytes = max_response_bytes
        self._lane_rest = lane_rest or LaneRest()
        self._gates = {
            "eastmoney": ProviderRequestGate(10, 0),
            "eastmoney_f10": ProviderRequestGate(10, 0),
            "eastmoney_push": ProviderRequestGate(2, 0.5),
            "tencent": ProviderRequestGate(4, 0.1),
            "sina": ProviderRequestGate(2, 0.3),
        }

    async def request_text(
        self,
        request: PublicHttpRequest,
        *,
        parser: Callable[[str], _T] | None = None,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None = None,
    ) -> str | _T:
        return await self.request(
            request,
            parser=parser or (lambda value: value),
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )

    async def request_json(
        self,
        request: PublicHttpRequest,
        *,
        timeout_seconds: float,
        cancellation_token: AbortSignal | None = None,
    ) -> object:
        def parse(value: str) -> object:
            parsed = json.loads(value.lstrip("\ufeff"))
            if not isinstance(parsed, (dict, list)):
                raise ValueError("JSON root must be an object or array")
            return parsed

        return await self.request(
            request,
            parser=parse,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )

    async def request(
        self,
        request: PublicHttpRequest,
        *,
        parser: Callable[[str], _T],
        timeout_seconds: float,
        cancellation_token: AbortSignal | None = None,
    ) -> _T:
        """Execute only an adapter-built request and parse its bounded body."""

        throw_if_aborted(cancellation_token)
        urls = (request.url, *request.fallback_urls)
        attempts = urls if len(urls) > 1 else (request.url, request.url)
        last_error: UpstreamUnavailable | None = None
        lane = request.lane or request.provider
        for index, url in enumerate(attempts[:2]):
            try:
                if self._lane_rest.remaining(lane) > 0:
                    raise self._lane_rest.refusal(lane)
                return await self._request_once(
                    request,
                    url=url,
                    parser=parser,
                    timeout_seconds=timeout_seconds,
                    cancellation_token=cancellation_token,
                )
            except UpstreamUnavailable as exc:
                if not exc.retryable:
                    raise
                last_error = exc
                if index == 0 and len(attempts) > 1:
                    continue
        if last_error is not None:
            raise last_error
        raise AssertionError("public request did not have a URL")

    async def _request_once(
        self,
        request: PublicHttpRequest,
        *,
        url: str,
        parser: Callable[[str], _T],
        timeout_seconds: float,
        cancellation_token: AbortSignal | None,
    ) -> _T:
        started = time.perf_counter()
        status = "failed"
        status_code: int | None = None
        response_size_bytes: int | None = None
        response_characters: int | None = None
        error_message: str | None = None
        error_category: StockApiErrorCategory | None = None
        lane = request.lane or request.provider
        gate = self._gates[lane]
        try:

            async def send() -> httpx.Response:
                try:
                    async with asyncio.timeout(timeout_seconds):
                        return await await_with_abort(
                            self._ensure_client().request(
                                request.method,
                                url,
                                headers=dict(request.headers),
                                content=request.body,
                            ),
                            cancellation_token,
                        )
                except (TimeoutError, httpx.TimeoutException) as exc:
                    raise UpstreamUnavailable(
                        "公开数据源请求超时。",
                        error_category="timeout",
                    ) from exc

            response = await gate.run(send, cancellation_token)
            self._lane_rest.answered(lane)
            status_code = response.status_code
            body = response.content
            response_size_bytes = len(body)
            if response_size_bytes > self._max_response_bytes:
                raise UpstreamUnavailable(
                    "上游响应超过大小限制。",
                    retryable=False,
                    error_category="invalid_response",
                )
            try:
                text = body.decode(request.encoding)
            except UnicodeDecodeError as exc:
                raise UpstreamUnavailable(
                    "上游响应编码无效。",
                    retryable=False,
                    error_category="invalid_response",
                ) from exc
            response_characters = len(text)
            if not 200 <= response.status_code < 300:
                retryable = response.status_code in _RETRYABLE_STATUSES
                raise UpstreamUnavailable(
                    f"公开数据源请求失败：HTTP {response.status_code}",
                    retryable=retryable,
                    error_category=(
                        "rate_limited"
                        if response.status_code == 429
                        else "upstream_http"
                    ),
                )
            try:
                result = parser(text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise UpstreamUnavailable(
                    "上游响应无法解析。",
                    retryable=False,
                    error_category="invalid_response",
                ) from exc
            status = "success"
            return result
        except asyncio.CancelledError:
            error_category = "cancelled"
            error_message = "request cancelled"
            raise
        except UpstreamUnavailable as exc:
            error_category = exc.error_category or "unknown"
            error_message = _public_log_error_message(exc)
            raise
        except (httpx.HTTPError, OSError) as exc:
            self._lane_rest.hung_up(lane)
            error_category = "network"
            error_message = _network_failure_message(exc)
            raise UpstreamUnavailable(
                error_message, error_category="network"
            ) from exc
        finally:
            await emit_stock_api_call_log(
                self._call_logger,
                StockApiCall(
                    provider=request.provider,
                    operation_id=normalize_public_stock_operation_id(request.operation),
                    endpoint=request.endpoint,
                    method=request.method,
                    parameters={},
                    status=status,
                    status_code=status_code,
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    response_size_bytes=response_size_bytes,
                    response_characters=response_characters,
                    error_category=error_category,
                    error_message=error_message,
                    source=current_stock_api_source(),
                    interface_name=None,
                    interface_identifier=None,
                ),
            )

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=False,
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


__all__ = [
    "LANE_REST_AFTER_FAILURES",
    "LANE_REST_SECONDS",
    "LaneRest",
    "ProviderRequestGate",
    "PublicHttpRequest",
    "PublicHttpTransport",
]
