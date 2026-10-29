"""Shared ORM registry and timestamps for independently registered models."""

from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Base(DeclarativeBase):
    """Single SQLAlchemy registry for the application."""
