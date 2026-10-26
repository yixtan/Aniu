"""Trading locks survive cancellation and process failures."""

import asyncio
import sys
from contextlib import suppress

import pytest

from backend.infra.integrations.trade_mutex import trade_mutex


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_release_owner_or_leak_lock(session_factory):
    entered = asyncio.Event()

    async def acquire():
        async with trade_mutex(session_factory):
            entered.set()

    async with trade_mutex(session_factory):
        waiter = asyncio.create_task(acquire())
        await asyncio.sleep(0.15)
        assert not entered.is_set()
        waiter.cancel()
        with suppress(asyncio.CancelledError):
            await waiter
        second = asyncio.create_task(acquire())
        await asyncio.sleep(0.15)
        assert not entered.is_set()
    await asyncio.wait_for(second, 2)
    assert entered.is_set()


@pytest.mark.asyncio
async def test_process_death_releases_lock(session_factory, engine):
    # Separate process, same SQLite lock file; killing it must not leave a lease
    # requiring cleanup or allow another buyer through before its death.
    path = engine.url.database + ".trade-lock"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
        "c.execute('BEGIN IMMEDIATE'); print('locked',flush=True); sys.stdin.read()",
        path,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    entered = asyncio.Event()

    async def acquire():
        async with trade_mutex(session_factory):
            entered.set()

    waiter = None
    try:
        assert await asyncio.wait_for(process.stdout.readline(), 3) == b"locked\n"
        waiter = asyncio.create_task(acquire())
        await asyncio.sleep(0.15)
        assert not entered.is_set()
        process.kill()
        await process.wait()
        await asyncio.wait_for(waiter, 2)
        assert entered.is_set()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        if waiter is not None:
            waiter.cancel()
            with suppress(asyncio.CancelledError):
                await waiter


@pytest.mark.asyncio
async def test_exception_releases_owner_lock(session_factory):
    with pytest.raises(RuntimeError, match="upstream"):
        async with trade_mutex(session_factory):
            raise RuntimeError("upstream")
    async with asyncio.timeout(2), trade_mutex(session_factory):
        pass
