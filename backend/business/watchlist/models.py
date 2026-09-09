"""Domain model for the operator's own watchlist.

The list is the operator's, not the agent's: it records which companies the
person running Aniu happens to follow or know well. It is one more input to a
decision, deliberately not a candidate pool — the agent's own stock selection
runs unchanged beside it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from backend.business.shared.trading import utc_now

MAX_NAME_LENGTH = 32
MAX_FOLLOWED = 10
"""Past a point a watchlist stops being a list of companies one follows.

It also bounds what a run has to read: every followed company is screened
every time, so the ceiling is on work per run as much as on tidiness.
"""

# Repeated here rather than imported from stock_api, which this layer may not
# depend on. The prefixes are a stable fact about the exchanges, and the
# notifications feature carries its own copy for the same reason.
_SYMBOL = re.compile(r"^(\d{6})(?:\.(SH|SZ))?$")
_SHANGHAI = re.compile(r"^(?:600|601|603|605|688)\d{3}$")
_SHENZHEN = re.compile(r"^(?:000|001|002|003|300|301)\d{3}$")


def normalize_symbol(value: str) -> str:
    """Return one A-share code in ``600519.SH`` form.

    The exchange suffix is derived rather than trusted, so ``600519``,
    ``600519.sh`` and ``600519.SH`` all name the same row and the uniqueness
    constraint means what it says.
    """

    normalized = value.strip().upper()
    match = _SYMBOL.fullmatch(normalized)
    if match is None:
        raise ValueError("证券代码格式无效，请使用 600519 或 600519.SH")
    code, suffix = match.groups()
    if _SHANGHAI.fullmatch(code):
        expected = "SH"
    elif _SHENZHEN.fullmatch(code):
        expected = "SZ"
    else:
        raise ValueError("仅支持沪深 A 股证券代码")
    if suffix is not None and suffix != expected:
        raise ValueError(f"{code} 属于{'上交所' if expected == 'SH' else '深交所'}")
    return f"{code}.{expected}"


@dataclass(frozen=True, slots=True)
class WatchlistItem:
    """One followed company. The name is a label for the operator's own eyes;
    the symbol is what the agent works with."""

    id: int
    symbol: str
    name: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        name = self.name.strip()
        if len(name) > MAX_NAME_LENGTH:
            raise ValueError(f"名称最多 {MAX_NAME_LENGTH} 个字符")
        object.__setattr__(self, "name", name)


__all__ = [
    "MAX_FOLLOWED",
    "MAX_NAME_LENGTH",
    "WatchlistItem",
    "normalize_symbol",
]
