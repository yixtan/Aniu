"""Ports for reading the attributed order record."""

from __future__ import annotations

from typing import Protocol

from backend.business.fill_record.models import AttributedOrder


class FillRecordRepositoryPort(Protocol):
    async def attributed_orders(self) -> list[AttributedOrder]: ...
