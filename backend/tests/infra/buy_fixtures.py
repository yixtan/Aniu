"""Synthetic source-backed buy approvals for integration regression tests."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, Mock

from backend.infra.integrations.buy_preflight import PrepareBuyTool
from backend.infra.integrations.buy_research_data import MARKET_ZONE, previous_sessions
from backend.infra.integrations.buy_research_tool import ReviewBuyTool
from backend.infra.repositories.tool_invocation_repo import ToolInvocationRepository
from backend.stock_api.public.contracts import (
    FinancialsRequest,
    StockMoneyFlowHistoryRequest,
    ValuationRequest,
)


def market_service(*, pe=10, pb=1, threshold_pe=20, threshold_pb=2):
    service = AsyncMock()

    async def execute(request, abort=None):
        if isinstance(request, (FinancialsRequest, StockMoneyFlowHistoryRequest)):
            now = datetime.now(UTC)
            if isinstance(request, StockMoneyFlowHistoryRequest):
                items = [
                    {"time": day, "main_net_inflow": 10000}
                    for day in previous_sessions(now.astimezone(MARKET_ZONE).date())
                ]
            else:
                today = now.astimezone(MARKET_ZONE).date()
                periods = sorted(
                    [
                        date(year, month, 1) - timedelta(days=1)
                        for year in range(today.year - 1, today.year + 2)
                        for month in (1, 4, 7, 10)
                        if date(year, month, 1) - timedelta(days=1) < today
                    ],
                    reverse=True,
                )[:2]
                items = [
                    {
                        "report_period": day.isoformat(),
                        "net_profit": 100,
                        "revenue": 1000,
                    }
                    for day in periods
                ]
            return {
                "meta": {
                    "source": "eastmoney",
                    "fetched_at": now.isoformat(),
                    "degraded": False,
                    "partial": False,
                },
                "data": {"symbol": request.symbol, "items": items},
            }
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


def decision_for(evidence):
    return {
        "thesis": "合成测试的估值与经营依据已完整核对",
        "counter_evidence": "合成测试已逐项核对全部相反证据",
        "sizing_rationale": "合成测试的最终数量符合风险限制",
        "memory_reviews": [
            {
                "id": rule["id"],
                "version": rule["version"],
                "assessment": "合成测试已检查该条经验适用性与反证",
            }
            for rule in evidence["context"]["memories"]
        ],
        "cancellation_reviews": [
            {"key": item["key"], "resolution": "合成测试以新来源核对原撤单原因已消除"}
            for item in evidence["context"]["cancellations"]
        ],
    }


async def record(factory, run_id, call_id, name, args, invoke):
    async with factory() as session:
        await ToolInvocationRepository(session).reserve(
            run_id=run_id, tool_call_id=call_id, tool_name=name, arguments=args
        )
        await session.commit()
    result = await invoke(run_id=run_id, tool_call_id=call_id, **args)
    async with factory() as session:
        await ToolInvocationRepository(session).complete(
            run_id=run_id, tool_call_id=call_id, result=result
        )
        await session.commit()
    return result


async def approve(
    factory,
    run_id,
    instruction,
    *,
    review_id="review",
    service=None,
    portfolio=None,
    **conditions,
):
    service = service if service is not None else market_service()
    if portfolio is None:
        portfolio = AsyncMock()
        portfolio.clear_read_cache = Mock()
        portfolio.get_orders.return_value = []
        portfolio.get_positions.return_value = []
    args = {"instruction": instruction, **conditions}
    collector_id = review_id + "-research"
    evidence = await record(
        factory,
        run_id,
        collector_id,
        "review_buy",
        args,
        ReviewBuyTool(service, portfolio, factory).run_for_call,
    )
    args.update(research_review_call_id=collector_id, decision=decision_for(evidence))
    await record(
        factory,
        run_id,
        review_id,
        "prepare_buy",
        args,
        PrepareBuyTool(service, factory, portfolio).run_for_call,
    )
    return service
