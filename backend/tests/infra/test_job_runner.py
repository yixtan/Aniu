"""Tests for APScheduler-backed job runner."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from backend.api.sse import StreamHub
from backend.bootstrap.runtime import AppRuntime
from backend.bootstrap.runtime_config import RuntimeConfig
from backend.bootstrap.schedule_handlers import (
    build_account_refresh_handler,
    build_market_analysis_handler,
)
from backend.business.account import AccountSnapshot
from backend.business.schedules import StrategySchedule
from backend.business.settings import (
    AppSettings,
    ModelProfile,
    SelectedModel,
    default_stage_settings,
)
from backend.business.shared.enums import TriggerSource
from backend.infra.repositories import (
    AccountCacheRepository,
    ModelProfileRepository,
    RunRepository,
    ScheduleRepository,
    SelectedModelRepository,
    SettingsRepository,
)
from backend.infra.scheduler import JobRunner
from backend.infra.scheduler.job_runner import ACCOUNT_REFRESH_JOB_ID
from backend.stock_api.mx import MxMoniClient


def _make_runner(session_factory, *, enabled: bool = True) -> JobRunner:
    runtime = AppRuntime(RuntimeConfig())
    runtime.stream_hub = StreamHub()
    runtime.mx_http_client = httpx.AsyncClient()
    return JobRunner(
        session_factory=session_factory,
        market_analysis_handler=build_market_analysis_handler(
            session_factory=session_factory,
            run_service_factory=runtime.run_service,
        ),
        account_refresh_handler=build_account_refresh_handler(
            session_factory=session_factory,
            account_service_factory=runtime.account_service,
        ),
        enabled=enabled,
    )


@pytest.mark.asyncio
async def test_job_runner_triggers_scheduled_market_analysis(
    session_factory,
    monkeypatch,
) -> None:
    async with session_factory() as session:
        profile = await ModelProfileRepository(session).create(
            ModelProfile(
                name="默认运行渠道",
                protocol="openai_chat_completions",
                model_name="gpt-4.1-mini",
                base_url="https://llm.example.com/v1",
                api_key="test-key",
            )
        )
        selected = (
            await SelectedModelRepository(session).replace_for_channel(
                profile.profile_id,
                [
                    SelectedModel(
                        channel_profile_id=profile.profile_id,
                        model_name="gpt-4.1-mini",
                        label="gpt-4.1-mini",
                        provider_id="gpt-4.1-mini",
                    )
                ],
            )
        )[0]
        stages = {
            stage_id: replace(
                stage,
                model_selected_model_id=selected.selected_model_id,
            )
            for stage_id, stage in default_stage_settings().items()
        }
        await SettingsRepository(session).save(
            AppSettings(mx_api_key="mx-test-key", stage_settings=stages)
        )
        schedule_repo = ScheduleRepository(session)
        schedule = await schedule_repo.add(
            StrategySchedule(
                enabled=True,
                interval_minutes=30,
            )
        )
        await session.commit()

    async def fake_get_account_snapshot(self: MxMoniClient):
        del self
        return AccountSnapshot(
            total_asset=100000.0,
            available_cash=25000.0,
            frozen_cash=0.0,
            market_value=75000.0,
            total_profit=5000.0,
            daily_profit=300.0,
        )

    async def fake_get_positions(self: MxMoniClient):
        del self
        return []

    async def fake_get_orders(self: MxMoniClient):
        del self
        return []

    monkeypatch.setattr(MxMoniClient, "get_account_snapshot", fake_get_account_snapshot)
    monkeypatch.setattr(MxMoniClient, "get_positions", fake_get_positions)
    monkeypatch.setattr(MxMoniClient, "get_orders", fake_get_orders)

    runner = _make_runner(session_factory)
    # Pin the clock: the scheduled path skips non-trading days, so a test that
    # reads the real date passes on a weekday and fails on a weekend.
    monkeypatch.setattr(
        runner,
        "_now_market_time",
        lambda: datetime(2026, 9, 11, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    await runner.sync_schedule(schedule)
    await runner.trigger_schedule(schedule.schedule_id)

    async with session_factory() as session:
        run_repo = RunRepository(session)
        runs = await run_repo.list_runs()
    await runner.shutdown()

    assert runs[0].trigger_source is TriggerSource.SCHEDULED
    assert runs[0].schedule_id == schedule.schedule_id


@pytest.mark.asyncio
async def test_job_runner_asks_the_handler_which_days_need_a_dream(
    session_factory,
) -> None:
    """The runner decides when to ask, not which day the answer is.

    Which days still need reflecting on is a question about run history, so it
    belongs to the handler. Reading a date off the clock here is what made a
    missed trigger unrecoverable.
    """

    calls = 0

    async def dream_handler(_lease_check):
        nonlocal calls
        calls += 1

    runner = JobRunner(
        session_factory=session_factory,
        memory_dream_handler=dream_handler,
    )

    next_run_at = await runner.sync_memory_dream_job()
    await runner.trigger_memory_dream()
    await runner.shutdown()

    assert next_run_at is not None
    assert next_run_at.astimezone(ZoneInfo("Asia/Shanghai")).hour == 0
    assert next_run_at.astimezone(ZoneInfo("Asia/Shanghai")).minute == 30
    assert calls == 1


@pytest.mark.asyncio
async def test_job_runner_reschedules_memory_dream_at_configured_time(
    session_factory,
) -> None:
    runner = JobRunner(session_factory=session_factory)

    first = await runner.sync_memory_dream_job("04:15")
    second = await runner.sync_memory_dream_job("21:45")
    await runner.shutdown()

    assert first is not None
    assert first.astimezone(ZoneInfo("Asia/Shanghai")).hour == 4
    assert first.astimezone(ZoneInfo("Asia/Shanghai")).minute == 15
    assert second is not None
    assert second.astimezone(ZoneInfo("Asia/Shanghai")).hour == 21
    assert second.astimezone(ZoneInfo("Asia/Shanghai")).minute == 45


@pytest.mark.asyncio
async def test_disabled_job_runner_does_not_register_or_execute_jobs(
    session_factory,
) -> None:
    runner = _make_runner(session_factory, enabled=False)

    next_run_at = await runner.sync_account_refresh_job()
    await runner.refresh_account_cache_now()
    await runner.shutdown()

    assert runner.enabled is False
    assert next_run_at is None


@pytest.mark.asyncio
async def test_job_runner_refreshes_account_cache(session_factory, monkeypatch) -> None:
    async with session_factory() as session:
        await SettingsRepository(session).save(AppSettings(mx_api_key="mx-test-key"))
        await session.commit()

    async def fake_post(self: MxMoniClient, endpoint: str, body: dict[str, object]):
        del body
        if endpoint.endswith("/balance"):
            return {
                "totalAssets": 5600.0,
                "availBalance": 1200.0,
                "frozenBalance": 0.0,
                "marketValue": 4400.0,
                "totalProfit": 60.0,
                "dailyProfit": 6.0,
                "currencyUnit": 1,
            }
        if endpoint.endswith("/positions"):
            return []
        if endpoint.endswith("/orders"):
            return []
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(MxMoniClient, "_post", fake_post)

    runner = _make_runner(session_factory)
    monkeypatch.setattr(
        runner,
        "_now_market_time",
        lambda: datetime(2026, 7, 31, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    next_run_at = await runner.sync_account_refresh_job()
    await runner.refresh_account_cache_now()

    async with session_factory() as session:
        cache_repo = AccountCacheRepository(session)
        snapshot = await cache_repo.get_account_snapshot()
    await runner.shutdown()

    assert next_run_at is not None
    assert snapshot is not None
    assert snapshot.total_asset == 5600.0


@pytest.mark.asyncio
async def test_job_runner_skips_automatic_refresh_on_non_trading_day(
    session_factory,
    monkeypatch,
) -> None:
    async with session_factory() as session:
        await SettingsRepository(session).save(AppSettings(mx_api_key="mx-test-key"))
        await session.commit()

    calls: list[str] = []

    async def fake_post(self: MxMoniClient, endpoint: str, body: dict[str, object]):
        del self, body
        calls.append(endpoint)
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(MxMoniClient, "_post", fake_post)

    runner = _make_runner(session_factory)
    monkeypatch.setattr(
        runner,
        "_now_market_time",
        lambda: datetime(2026, 2, 17, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    await runner.refresh_account_cache_now()

    async with session_factory() as session:
        cache_repo = AccountCacheRepository(session)
        snapshot = await cache_repo.get_account_snapshot()

    assert calls == []
    assert snapshot is None


@pytest.mark.asyncio
async def test_job_runner_refreshes_account_cache_on_trading_day(
    session_factory,
    monkeypatch,
) -> None:
    async with session_factory() as session:
        await SettingsRepository(session).save(AppSettings(mx_api_key="mx-test-key"))
        await session.commit()

    calls: list[str] = []

    async def fake_post(self: MxMoniClient, endpoint: str, body: dict[str, object]):
        del self, body
        calls.append(endpoint)
        if endpoint.endswith("/balance"):
            return {
                "totalAssets": 7800.0,
                "availBalance": 1800.0,
                "frozenBalance": 0.0,
                "marketValue": 6000.0,
                "totalProfit": 80.0,
                "dailyProfit": 8.0,
                "currencyUnit": 1,
            }
        if endpoint.endswith("/positions"):
            return []
        if endpoint.endswith("/orders"):
            return []
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(MxMoniClient, "_post", fake_post)

    runner = _make_runner(session_factory)
    monkeypatch.setattr(
        runner,
        "_now_market_time",
        lambda: datetime(2026, 4, 27, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    await runner.refresh_account_cache_now()

    async with session_factory() as session:
        cache_repo = AccountCacheRepository(session)
        snapshot = await cache_repo.get_account_snapshot()

    assert calls == [
        "/api/claw/mockTrading/balance",
        "/api/claw/mockTrading/positions",
        "/api/claw/mockTrading/orders",
    ]
    assert snapshot is not None
    assert snapshot.total_asset == 7800.0


@pytest.mark.asyncio
async def test_account_refresh_runs_every_three_minutes_through_the_session(
    session_factory,
) -> None:
    """Fills are only visible via this refresh, so its cadence is the alert lag."""

    runner = _make_runner(session_factory)
    await runner.sync_account_refresh_job()
    job = runner._scheduler.get_job(ACCOUNT_REFRESH_JOB_ID)
    await runner.shutdown()

    assert job is not None
    fields = {field.name: str(field) for field in job.trigger.fields}
    assert fields["minute"] == "*/3"
    assert fields["hour"] == "9-11,13-15"
    assert fields["day_of_week"] == "mon-fri"


@pytest.mark.asyncio
async def test_account_refresh_fire_times_bound_the_fill_notification_lag(
    session_factory,
) -> None:
    runner = _make_runner(session_factory)
    await runner.sync_account_refresh_job()
    job = runner._scheduler.get_job(ACCOUNT_REFRESH_JOB_ID)
    await runner.shutdown()

    assert job is not None
    # Walk a mid-session Thursday and confirm the cadence stays even.
    moment = datetime(2026, 7, 30, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    fire_times = []
    for _ in range(4):
        moment = job.trigger.get_next_fire_time(None, moment)
        fire_times.append(moment.astimezone(ZoneInfo("Asia/Shanghai")))
        moment = moment + timedelta(seconds=1)

    gaps = {
        int((later - earlier).total_seconds() // 60)
        for earlier, later in zip(fire_times, fire_times[1:], strict=False)
    }
    assert gaps == {3}


@pytest.mark.asyncio
async def test_account_refresh_skips_slots_outside_the_trading_sessions(
    session_factory,
    monkeypatch,
) -> None:
    """The cron fires across whole hours; the sessions start and end mid-hour."""

    async with session_factory() as session:
        await SettingsRepository(session).save(AppSettings(mx_api_key="mx-test-key"))
        await session.commit()

    async def fake_post(self: MxMoniClient, endpoint: str, body: dict[str, object]):
        del self, body
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(MxMoniClient, "_post", fake_post)

    runner = _make_runner(session_factory)
    monkeypatch.setattr(
        runner,
        "_now_market_time",
        # A trading Friday, but nine minutes before the opening bell.
        lambda: datetime(2026, 9, 11, 9, 21, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    await runner.refresh_account_cache_now()

    async with session_factory() as session:
        snapshot = await AccountCacheRepository(session).get_account_snapshot()

    assert snapshot is None


def test_fill_watch_window_keeps_a_tail_past_each_close() -> None:
    """A fill can settle just after the bell, so the window outlives the session."""

    def at(hour: int, minute: int) -> datetime:
        return datetime(
            2026, 9, 11, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai")
        )

    can_observe = JobRunner._can_still_observe_a_fill

    assert not can_observe(at(9, 27))
    assert can_observe(at(9, 30))
    assert can_observe(at(11, 30))
    assert can_observe(at(11, 45))
    assert not can_observe(at(11, 48))
    assert can_observe(at(15, 0))
    assert can_observe(at(15, 15))
    assert not can_observe(at(15, 18))
    # The window is a property of the session, not of the clock: a holiday that
    # falls on a weekday has no session for the grace period to hang off.
    assert not can_observe(
        datetime(2026, 10, 1, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    )


@pytest.mark.asyncio
async def test_job_runner_skips_a_scheduled_run_on_a_statutory_holiday(
    session_factory,
    monkeypatch,
) -> None:
    """The cron excludes weekends but not holidays, so the handler must."""

    async with session_factory() as session:
        schedule = await ScheduleRepository(session).add(
            StrategySchedule(enabled=True, interval_minutes=30)
        )
        await session.commit()

    runner = _make_runner(session_factory)
    monkeypatch.setattr(
        runner,
        "_now_market_time",
        # National Day, a Thursday: `day_of_week="mon-fri"` lets it through.
        lambda: datetime(2026, 10, 1, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    await runner.trigger_schedule(schedule.schedule_id)

    async with session_factory() as session:
        runs = await RunRepository(session).list_runs()
    await runner.shutdown()

    assert runs == []
