"""Route candidates and the adapter bundle, shared by every route table.

A leaf module so that route tables living outside ``router.py`` can build
candidates without importing the router, which imports them: ``router.py``
reached 845 lines with the original catalog, and the architecture test caps a
file at 1000 and forbids import cycles.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from backend.stock_api.public.cancellation import CancellationToken as AbortSignal
from backend.stock_api.public.contracts import ProviderName, PublicStockRequest
from backend.stock_api.public.normalizers.common import NormalizedData
from backend.stock_api.public.providers.eastmoney import EastMoneyAdapter
from backend.stock_api.public.providers.sina import SinaAdapter
from backend.stock_api.public.providers.tencent import TencentAdapter
from backend.stock_api.public.providers.ths import ThsAdapter

_Operation = Callable[[float, AbortSignal | None], Awaitable[object]]
_Normalizer = Callable[[object], NormalizedData]


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    provider: ProviderName
    endpoint: str
    execute: _Operation
    normalize: _Normalizer
    degraded: bool = False
    warning: str | None = None
    fallback_on_empty: bool = False
    reserve_seconds: float = 0.0
    """Of the operation's total, what this candidate leaves for the next one."""


@dataclass(frozen=True, slots=True)
class PublicProviderAdapters:
    eastmoney: EastMoneyAdapter
    tencent: TencentAdapter
    sina: SinaAdapter
    # None only in tests written before 同花顺 was a source; production always
    # passes one, and routes skip their 同花顺 candidates without it.
    ths: ThsAdapter | None = None


def route_candidate(
    provider: ProviderName,
    endpoint: str,
    method: Callable[..., Awaitable[object]],
    request: PublicStockRequest,
    normalize: _Normalizer,
    *,
    degraded: bool = False,
    warning: str | None = None,
    fallback_on_empty: bool = False,
    reserve_seconds: float = 0.0,
) -> RouteCandidate:
    async def execute(
        timeout_seconds: float, cancellation_token: AbortSignal | None
    ) -> object:
        return await method(
            request,
            timeout_seconds=timeout_seconds,
            cancellation_token=cancellation_token,
        )

    return RouteCandidate(
        provider=provider,
        endpoint=endpoint,
        execute=execute,
        normalize=normalize,
        degraded=degraded,
        warning=warning,
        fallback_on_empty=fallback_on_empty,
        reserve_seconds=reserve_seconds,
    )


__all__ = ["PublicProviderAdapters", "RouteCandidate", "route_candidate"]
