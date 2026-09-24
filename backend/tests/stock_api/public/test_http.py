from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest

from backend.stock_api.models import StockApiCall
from backend.stock_api.public.errors import UpstreamUnavailable
from backend.stock_api.public.http import (
    LANE_REST_AFTER_FAILURES,
    LANE_REST_SECONDS,
    LaneRest,
    PublicHttpRequest,
    PublicHttpTransport,
)


@dataclass
class CallLog:
    values: list[StockApiCall]

    async def __call__(self, call: StockApiCall) -> None:
        self.values.append(call)


def request(*, fallback_urls: tuple[str, ...] = ()) -> PublicHttpRequest:
    return PublicHttpRequest(
        provider="tencent",
        operation="quote.snapshot",
        endpoint="test_endpoint",
        url="https://primary.example.test/data",
        fallback_urls=fallback_urls,
        parameters={"symbol": "600519.SH"},
        headers={},
    )


def test_only_eastmoney_push_lane_has_a_start_interval() -> None:
    transport = PublicHttpTransport(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))
    )

    assert transport._gates["eastmoney_push"].maximum_concurrency == 2
    assert transport._gates["eastmoney_push"].minimum_start_interval == 0.5
    assert transport._gates["eastmoney"].maximum_concurrency == 10
    assert transport._gates["eastmoney"].minimum_start_interval == 0
    assert transport._gates["eastmoney_f10"].maximum_concurrency == 10
    assert transport._gates["eastmoney_f10"].minimum_start_interval == 0


@pytest.mark.asyncio
async def test_http_retries_primary_then_first_fallback_only() -> None:
    urls: list[str] = []
    logs: list[StockApiCall] = []

    def handler(raw_request: httpx.Request) -> httpx.Response:
        urls.append(str(raw_request.url))
        if len(urls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, text='{"ok":true}')

    transport = PublicHttpTransport(
        transport=httpx.MockTransport(handler), call_logger=CallLog(logs)
    )
    transport._gates["tencent"].minimum_start_interval = 0
    result = await transport.request_json(
        request(
            fallback_urls=(
                "https://fallback-one.example.test/data",
                "https://fallback-two.example.test/data",
            )
        ),
        timeout_seconds=1,
    )
    assert result == {"ok": True}
    assert urls == [
        "https://primary.example.test/data",
        "https://fallback-one.example.test/data",
    ]
    assert [call.error_category for call in logs] == ["upstream_http", None]
    assert logs[0].response_characters == 0
    assert logs[1].response_characters == len('{"ok":true}')
    await transport.aclose()


@pytest.mark.asyncio
async def test_http_parser_failure_is_invalid_response_and_not_retried() -> None:
    logs: list[StockApiCall] = []
    transport = PublicHttpTransport(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="ok")),
        call_logger=CallLog(logs),
    )
    transport._gates["tencent"].minimum_start_interval = 0

    with pytest.raises(Exception):
        await transport.request_text(
            request(), parser=lambda _: cast_never(), timeout_seconds=1
        )
    assert len(logs) == 1
    assert logs[0].error_category == "invalid_response"
    await transport.aclose()


def cast_never() -> str:
    raise KeyError("malformed")


@pytest.mark.asyncio
async def test_http_decoding_failure_is_invalid_response() -> None:
    logs: list[StockApiCall] = []

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"\xff",
            headers={"content-type": "text/plain"},
        )

    transport = PublicHttpTransport(
        transport=httpx.MockTransport(handler),
        call_logger=CallLog(logs),
    )
    transport._gates["tencent"].minimum_start_interval = 0
    with pytest.raises(Exception):
        await transport.request_json(request(), timeout_seconds=1)
    assert len(logs) == 1
    assert logs[0].error_category == "invalid_response"
    await transport.aclose()


@pytest.mark.asyncio
async def test_http_timeout_category_is_stable() -> None:
    logs: list[StockApiCall] = []

    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout")

    transport = PublicHttpTransport(
        transport=httpx.MockTransport(handler), call_logger=CallLog(logs)
    )
    transport._gates["tencent"].minimum_start_interval = 0
    with pytest.raises(Exception):
        await transport.request_json(request(), timeout_seconds=1)
    assert len(logs) == 2
    assert [call.error_category for call in logs] == ["timeout", "timeout"]
    await transport.aclose()


def _hanging_up(counter: list[int]):
    def handler(_: httpx.Request) -> httpx.Response:
        counter.append(1)
        raise httpx.RemoteProtocolError(
            "Server disconnected without sending a response."
        )

    return handler


@pytest.mark.asyncio
async def test_a_hang_up_is_named_not_just_counted() -> None:
    """2026-09-24: push2 had hung up on most requests for two days, and every
    one of them read 「公开数据源网络请求失败」 — a server that answers with a
    closed connection and one that cannot be reached looked the same."""

    logs: list[StockApiCall] = []
    transport = PublicHttpTransport(
        transport=httpx.MockTransport(_hanging_up([])), call_logger=CallLog(logs)
    )
    transport._gates["tencent"].minimum_start_interval = 0

    with pytest.raises(UpstreamUnavailable) as raised:
        await transport.request_json(request(), timeout_seconds=1)

    message = str(raised.value)
    assert "RemoteProtocolError" in message
    assert "没有回应就断开" in message
    # The class, never the httpx text: that can carry the request URL.
    assert "primary.example.test" not in message
    assert logs[-1].error_category == "network"
    assert logs[-1].error_message == message
    await transport.aclose()


@pytest.mark.asyncio
async def test_a_lane_that_keeps_hanging_up_is_rested_without_being_asked() -> None:
    """Asking a host that is turning us away does not help, and may be part of
    why it keeps doing so. The third call must not reach it at all."""

    sent: list[int] = []
    transport = PublicHttpTransport(
        transport=httpx.MockTransport(_hanging_up(sent)),
        lane_rest=LaneRest(clock=lambda: 1000.0),
    )
    transport._gates["tencent"].minimum_start_interval = 0

    for _ in range(2):  # each call is sent twice, so this is four hang-ups
        with pytest.raises(UpstreamUnavailable):
            await transport.request_json(request(), timeout_seconds=1)
    assert len(sent) == LANE_REST_AFTER_FAILURES

    with pytest.raises(UpstreamUnavailable) as raised:
        await transport.request_json(request(), timeout_seconds=1)

    assert len(sent) == LANE_REST_AFTER_FAILURES
    assert "暂停" in str(raised.value)
    assert "没有发出请求" in str(raised.value)
    # Still retryable, so the router can fall back to another provider.
    assert raised.value.retryable
    await transport.aclose()


def test_an_answer_of_any_kind_clears_the_count() -> None:
    """A host that answers — even with an error status — is not hanging up."""

    rest = LaneRest(clock=lambda: 0.0)
    for _ in range(LANE_REST_AFTER_FAILURES - 1):
        rest.hung_up("eastmoney_push")
    rest.answered("eastmoney_push")
    for _ in range(LANE_REST_AFTER_FAILURES - 1):
        rest.hung_up("eastmoney_push")

    assert rest.remaining("eastmoney_push") == 0


def test_after_a_rest_the_first_request_is_a_probe() -> None:
    now = [0.0]
    rest = LaneRest(clock=lambda: now[0])
    for _ in range(LANE_REST_AFTER_FAILURES):
        rest.hung_up("eastmoney_push")
    assert rest.remaining("eastmoney_push") == LANE_REST_SECONDS

    now[0] += LANE_REST_SECONDS
    assert rest.remaining("eastmoney_push") == 0

    rest.hung_up("eastmoney_push")  # one hang-up back is enough to rest again
    assert rest.remaining("eastmoney_push") == LANE_REST_SECONDS

    now[0] += LANE_REST_SECONDS
    rest.answered("eastmoney_push")  # while an answer clears it entirely
    for _ in range(LANE_REST_AFTER_FAILURES - 1):
        rest.hung_up("eastmoney_push")
    assert rest.remaining("eastmoney_push") == 0


def test_lanes_rest_independently() -> None:
    """push2 hanging up is no reason to stop asking push2his."""

    rest = LaneRest(clock=lambda: 0.0)
    for _ in range(LANE_REST_AFTER_FAILURES):
        rest.hung_up("eastmoney_push")

    assert rest.remaining("eastmoney_push") > 0
    assert rest.remaining("eastmoney") == 0


@pytest.mark.asyncio
async def test_a_timeout_neither_rests_a_lane_nor_clears_it() -> None:
    """A timeout may be a filter dropping us or only a slow network; it is not
    this rule's place to guess which."""

    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout")

    rest = LaneRest(clock=lambda: 0.0)
    transport = PublicHttpTransport(
        transport=httpx.MockTransport(handler), lane_rest=rest
    )
    transport._gates["tencent"].minimum_start_interval = 0
    for _ in range(LANE_REST_AFTER_FAILURES):
        with pytest.raises(UpstreamUnavailable):
            await transport.request_json(request(), timeout_seconds=1)

    assert rest.remaining("tencent") == 0
    await transport.aclose()
