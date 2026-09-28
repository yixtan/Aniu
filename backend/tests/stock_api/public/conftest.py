"""Shared fixtures for the public stock-data tests."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

import pytest


@pytest.fixture
def host_zone_is_not_beijing() -> Iterator[None]:
    """Run the test with the process in UTC, so only an explicit Shanghai zone
    can turn 同花顺's epoch seconds into Beijing times.

    This machine's own zone is Asia/Shanghai: a clock read in the host's zone
    passes here and fails only on CI. Restored by hand, with a last tzset,
    rather than through monkeypatch, which would put TZ back after this
    teardown had already re-read it and leave later tests in UTC.
    """

    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is POSIX-only")
    before = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()
