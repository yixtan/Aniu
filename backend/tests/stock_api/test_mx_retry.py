"""How long MX refusals are waited out, and which messages count as one."""

from __future__ import annotations

import pytest

from backend.stock_api.mx.http import MxHttpTransport
from backend.stock_api.mx.moni import MxMoniClient
from backend.stock_api.mx.retry import (
    MX_RATE_LIMIT_BACKOFF,
    MX_RATE_LIMIT_RETRIES,
    MX_REQUEST_INTERVAL,
    MxRequestGate,
    is_mx_rate_limit_message,
    mx_retry_delay,
)


@pytest.mark.parametrize(
    "message",
    [
        "请求频率过高，请稍后再试",
        # 2026-09-24 09:30, the data endpoint's wording for the same thing.
        "触发限流，请稍后再试",
    ],
)
def test_both_wordings_of_a_refusal_are_waited_out(message: str) -> None:
    assert is_mx_rate_limit_message(message)


def test_a_spent_quota_is_never_mistaken_for_a_refusal() -> None:
    """「限流」 is broad enough to turn up in a quota message, and waiting a few
    seconds will not bring a day's quota back."""

    assert not is_mx_rate_limit_message("调用次数已达到上限，已触发限流")


def test_the_waits_outlast_the_window_that_beat_the_old_ones() -> None:
    """0.5 s then 1 s were both spent inside MX's window on 09-24, and the
    model spent ninety seconds retrying one sell by hand."""

    delays = [
        mx_retry_delay(attempt, base_delay=MX_RATE_LIMIT_BACKOFF)
        for attempt in range(1, MX_RATE_LIMIT_RETRIES + 1)
    ]

    assert delays == [2.0, 4.0, 8.0]


def test_every_mx_client_starts_from_the_same_pacing() -> None:
    """Data, portfolio and orders share one MX quota, so they share one gap."""

    assert MxRequestGate().min_interval == MX_REQUEST_INTERVAL == 0.5
    for client in (MxHttpTransport(), MxMoniClient()):
        assert client.request_interval == MX_REQUEST_INTERVAL
        assert client.rate_limit_retries == MX_RATE_LIMIT_RETRIES
        assert client.rate_limit_backoff == MX_RATE_LIMIT_BACKOFF
