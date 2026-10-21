"""Fill in the closing values of trading days nobody recorded.

    python -m scripts.reconstruct_daily_equity            # look only
    python -m scripts.reconstruct_daily_equity --write    # then keep them

(with the backend interpreter, .aniu/local/.venv/bin/python)

Without --write nothing is stored: it prints every day it worked out, next to
the recorded close where there is one, so the result can be judged first.
It refuses to write when:
- the fills do not add up to the holdings the broker reports — an order is
  missing, and every day after it would be wrong;
- a recorded close differs from its reconstruction by more than
  --max-error, which defaults to 0.1%.

A recorded day is never overwritten; an earlier reconstruction is. Only
recorded days read after the bell are used to judge the result: one read at
10:00 is that moment, not the close the reconstruction is of. Daily
closes are fetched one symbol at a time with a pause in between, from the
same public quote service the app uses.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from backend.business.performance import DailyEquity, EquitySource
from backend.business.performance.reconstruction import (
    fee_scale,
    holdings_after,
    reconstruct,
)
from backend.infra.calendar import TradingCalendar2026
from backend.infra.db.models import AccountCacheStateModel, AccountPositionCacheModel
from backend.infra.db.session import (
    build_database_url,
    create_engine,
    create_session_factory,
    init_db,
)
from backend.infra.repositories import AccountFactsRepository, DailyEquityRepository
from backend.stock_api.public import KlineRequest, StockMarketDataService

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
# A day's closes are final a while after the bell; the same line the curve
# uses for the index.
CLOSES_SETTLED = time(15, 30)
SESSION_CLOSES = time(15, 0)
PAUSE_BETWEEN_SYMBOLS = 1.5
MAX_BARS = 300


def trading_days(first: date, last: date) -> list[date]:
    calendar = TradingCalendar2026()
    days: list[date] = []
    day = first
    while day <= last:
        if calendar.last_trading_day_on_or_before(day) == day:
            days.append(day)
        day += timedelta(days=1)
    return days


def is_close(day: DailyEquity) -> bool:
    captured = day.captured_at.astimezone(MARKET_TIMEZONE)
    return captured.date() > day.trade_date or captured.time() >= SESSION_CLOSES


def last_settled_day(now: datetime) -> date:
    local = now.astimezone(MARKET_TIMEZONE)
    today = local.date()
    if local.time() >= CLOSES_SETTLED:
        return TradingCalendar2026().last_trading_day_on_or_before(today)
    return TradingCalendar2026().last_trading_day_on_or_before(
        today - timedelta(days=1)
    )


async def daily_closes(
    symbols: list[str], *, first: date, last: date
) -> dict[str, dict[date, float]]:
    closes: dict[str, dict[date, float]] = {}
    async with httpx.AsyncClient() as client:
        service = StockMarketDataService.create(http_client=client)
        for index, symbol in enumerate(symbols):
            if index:
                await asyncio.sleep(PAUSE_BETWEEN_SYMBOLS)
            result = await service.execute(
                KlineRequest(
                    symbol=symbol,
                    period="day",
                    adjust="none",
                    start_date=first.isoformat(),
                    end_date=last.isoformat(),
                    limit=MAX_BARS,
                )
            )
            data = result.get("data")
            bars = data.get("bars") if isinstance(data, dict) else None
            closes[symbol] = {
                date.fromisoformat(str(bar["time"])[:10]): float(bar["close"])
                for bar in (bars if isinstance(bars, list) else [])
                if isinstance(bar, dict)
                and isinstance(bar.get("time"), str)
                and isinstance(bar.get("close"), (int, float))
            }
            print(f"  {symbol}: {len(closes[symbol])} 天收盘价")
    return closes


async def run(*, write: bool, max_error: float) -> int:
    engine = create_engine(build_database_url())
    await init_db(engine)
    sessions = create_session_factory(engine)
    try:
        async with sessions() as session:
            facts = AccountFactsRepository(session)
            equity = DailyEquityRepository(session)
            start = await facts.account_start()
            if start is None:
                print("账户还没有开户日期和初始资金，先在首页刷新一次账户。")
                return 1
            fills = await facts.day_fills()
            state = (
                await session.scalars(select(AccountCacheStateModel).limit(1))
            ).first()
            if state is None or state.available_cash is None:
                print("还没有账户资金数据，先在首页刷新一次账户。")
                return 1
            actual_cash = float(state.available_cash) + float(state.frozen_cash or 0.0)
            positions = {
                row.symbol: int(row.quantity)
                for row in (
                    await session.scalars(select(AccountPositionCacheModel))
                ).all()
                if row.quantity
            }
            replayed = holdings_after(fills)
            if replayed != positions:
                print("成交记录推出来的持仓和实际持仓对不上，不能推算：")
                print(f"  推算：{replayed}")
                print(f"  实际：{positions}")
                return 1

            last = last_settled_day(datetime.now(tz=UTC))
            days = trading_days(start.open_date, last)
            symbols = sorted({fill.symbol for fill in fills})
            print(
                f"从 {start.open_date} 到 {last}，"
                f"{len(days)} 个交易日，{len(symbols)} 只股票"
            )
            closes = await daily_closes(symbols, first=start.open_date, last=last)

            scale = fee_scale(
                fills,
                initial_capital=start.initial_capital,
                actual_cash=actual_cash,
            )
            print(f"手续费校准系数：{scale:.4f}（按标准费率算出的手续费再乘以它）")
            if not 0.8 <= scale <= 1.5:
                print(
                    "  这个系数偏离 1 太多，可能有分红、送股或漏掉的成交，结果要慎用。"
                )

            values, missing = reconstruct(
                days,
                [fill for fill in fills if fill.trade_date <= last],
                closes,
                initial_capital=start.initial_capital,
                scale=scale,
                captured_at=datetime.now(tz=UTC),
            )
            for gap in missing:
                print(f"  {gap.trade_date} {gap.symbol} 没有收盘价，用了前一个收盘价")

            recorded = {
                day.trade_date: day
                for day in await equity.list_days()
                if day.source is EquitySource.RECORDED
            }
            closes_recorded = {
                trade_date: day for trade_date, day in recorded.items() if is_close(day)
            }
            worst = 0.0
            print()
            print(
                "日期          推算总资产      持仓市值    仓位    实际记录        差额"
            )
            for value in values:
                actual = closes_recorded.get(value.trade_date)
                line = (
                    f"{value.trade_date}  {value.total_asset:>14,.2f}  "
                    f"{value.market_value:>10,.0f}  "
                    f"{value.market_value / value.total_asset:>6.1%}"
                )
                if actual is not None:
                    difference = value.total_asset - actual.total_asset
                    worst = max(worst, abs(difference) / actual.total_asset)
                    line += f"  {actual.total_asset:>14,.2f}  {difference:>+10,.2f}"
                print(line)
            print()
            if closes_recorded:
                print(f"和实际记录的最大偏差：{worst:.4%}")
            else:
                print("还没有实际记录可以对照，只能靠手续费校准。")
            if worst > max_error:
                print(f"偏差超过 {max_error:.2%}，不写入。")
                return 1

            to_write: list[DailyEquity] = [
                value for value in values if value.trade_date not in recorded
            ]
            if not write:
                print(
                    f"预览结束：可以补上 {len(to_write)} 天。"
                    "确认无误后加 --write 写入。"
                )
                return 0
            for value in to_write:
                await equity.record_day(value)
            await session.commit()
            print(f"已写入 {len(to_write)} 天推算值。")
            return 0
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="用成交记录推算没有记录的交易日收盘总资产。"
    )
    parser.add_argument("--write", action="store_true", help="把推算结果写进数据库")
    parser.add_argument(
        "--max-error",
        type=float,
        default=0.001,
        help="和实际记录相比允许的最大偏差（比例），默认 0.001 即 0.1%%",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(write=args.write, max_error=args.max_error)))


if __name__ == "__main__":
    main()
