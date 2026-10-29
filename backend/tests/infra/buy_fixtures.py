"""Synthetic source-backed buy approvals for integration regression tests."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from backend.infra.integrations.buy_preflight import PrepareBuyTool
from backend.infra.repositories.tool_invocation_repo import ToolInvocationRepository
from backend.stock_api.public.contracts import ValuationRequest


def market_service(*, pe=10, pb=1, threshold_pe=20, threshold_pb=2):
    service = AsyncMock()

    async def execute(request, abort=None):
        if isinstance(request, ValuationRequest):
            return {
                "data": {
                    "symbol": request.symbol,
                    "items": [
                        {
                            "metric": metric,
                            "window_years": year,
                            "percentile_30_threshold": threshold,
                        }
                        for metric, threshold in [
                            ("pe", threshold_pe),
                            ("pb", threshold_pb),
                        ]
                        for year in [1, 3, 5, 10]
                    ],
                }
            }
        return {
            "data": {
                "quotes": [
                    {
                        "symbol": request.symbols[0],
                        "price": 20,
                        "pe_ttm": pe,
                        "pb": pb,
                        "market_time": datetime.now(UTC).isoformat(),
                    }
                ]
            }
        }

    service.execute_fresh.side_effect = execute
    return service


async def approve(
    factory, run_id, instruction, *, review_id="review", service=None, **conditions
):
    service = service if service is not None else market_service()
    args = {"instruction": instruction, **conditions}
    async with factory() as session:
        repo = ToolInvocationRepository(session)
        await repo.reserve(
            run_id=run_id,
            tool_call_id=review_id,
            tool_name="prepare_buy",
            arguments=args,
        )
        await session.commit()
    result = await PrepareBuyTool(service).run_for_call(
        run_id=run_id,
        tool_call_id=review_id,
        **args,
    )
    async with factory() as session:
        await ToolInvocationRepository(session).complete(
            run_id=run_id,
            tool_call_id=review_id,
            result=result,
        )
        await session.commit()
    return service
