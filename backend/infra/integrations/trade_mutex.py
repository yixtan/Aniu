"""Cross-process trading serialization independent of application DB writes."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


@asynccontextmanager
async def trade_mutex(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[None]:
    # Derive identity from the bound database, never from model arguments.
    bind = factory.kw.get("bind")
    if not isinstance(bind, AsyncEngine) or bind.url.get_backend_name() != "sqlite":
        raise ValueError("交易保护需要文件 SQLite 数据库")
    database = bind.url.database
    if not database or database == ":memory:" or bind.url.query.get("mode") == "memory":
        raise ValueError("交易保护需要文件 SQLite 数据库")
    path = Path(database).resolve()
    lock_path = path.with_name(path.name + ".trade-lock")
    # This file stores no account data. SQLite releases its lock on process death.
    fd = lock_path.open("ab")
    fd.close()
    lock_path.chmod(0o600)
    engine = create_async_engine(
        URL.create("sqlite+aiosqlite", database=str(lock_path)),
        connect_args={"timeout": 0.05},
        poolclass=NullPool,
    )
    try:
        async with engine.connect() as connection:
            while True:
                try:
                    await connection.execute(text("BEGIN IMMEDIATE"))
                    break
                except OperationalError as exc:
                    await connection.rollback()
                    if (
                        getattr(exc.orig, "sqlite_errorcode", None)
                        != sqlite3.SQLITE_BUSY
                    ):
                        raise
                    # Short waits remain cancellable; no sleeping application writer.
                    await asyncio.sleep(0.05)
            try:
                yield
            finally:
                await connection.rollback()
    finally:
        await engine.dispose()
