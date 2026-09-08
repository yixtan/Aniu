"""Away mode: the switch, its midnight expiry, and what it does on a run."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from backend.business.away import AwayMode, AwayModeService, market_date

# 2026-09-08 09:00 in Shanghai. Expressed in UTC to prove the domain converts.
MORNING = datetime(2026, 9, 8, 1, 0, tzinfo=UTC)
# 23:30 the same Shanghai day; still 2026-09-08 locally.
LATE_EVENING = datetime(2026, 9, 8, 15, 30, tzinfo=UTC)
# 00:30 the next Shanghai day.
AFTER_MIDNIGHT = datetime(2026, 9, 8, 16, 30, tzinfo=UTC)


class FakeRepo:
    def __init__(self, mode: AwayMode | None = None) -> None:
        self.mode = mode

    async def get(self) -> AwayMode | None:
        return self.mode

    async def save(self, mode: AwayMode) -> AwayMode:
        self.mode = mode
        return mode


class FakeMailResult:
    def __init__(self, delivered: bool, message: str = "") -> None:
        self.delivered = delivered
        self.message = message


class FakeMailer:
    def __init__(self, *, delivered: bool = True, raises: bool = False) -> None:
        self.calls: list[int] = []
        self._delivered = delivered
        self._raises = raises

    async def send_run_report(self, run_id: int) -> FakeMailResult:
        if self._raises:
            raise RuntimeError("邮件服务不可用")
        self.calls.append(run_id)
        return FakeMailResult(self._delivered, "发送失败：HTTP 500")


def build(
    mode: AwayMode | None = None,
    *,
    now: datetime = MORNING,
    mailer: FakeMailer | None = None,
) -> tuple[AwayModeService, FakeRepo, FakeMailer]:
    repo = FakeRepo(mode)
    post = mailer or FakeMailer()
    return AwayModeService(repo=repo, mailer=post, now_provider=lambda: now), repo, post


def test_market_date_reads_the_shanghai_day_not_the_utc_one() -> None:
    # 16:30 UTC is already the next day in Shanghai; a naive UTC .date() would
    # disagree, and away mode would then expire eight hours late.
    assert market_date(LATE_EVENING) == date(2026, 9, 8)
    assert market_date(AFTER_MIDNIGHT) == date(2026, 9, 9)


def test_the_switch_expires_when_the_market_day_turns_over() -> None:
    mode = AwayMode(active_date=date(2026, 9, 8))

    assert mode.is_active(LATE_EVENING) is True
    assert mode.is_active(AFTER_MIDNIGHT) is False


def test_a_mode_that_was_never_switched_on_is_off() -> None:
    assert AwayMode().is_active(MORNING) is False


async def test_switching_on_stamps_todays_market_day() -> None:
    service, repo, _ = build()

    state = await service.set_enabled(True)

    assert state.enabled is True
    assert repo.mode is not None
    assert repo.mode.active_date == date(2026, 9, 8)


async def test_switching_off_clears_the_day() -> None:
    service, repo, _ = build(AwayMode(active_date=date(2026, 9, 8)))

    state = await service.set_enabled(False)

    assert state.enabled is False
    assert repo.mode is not None
    assert repo.mode.active_date is None


async def test_yesterdays_switch_reports_off_without_anything_clearing_it() -> None:
    service, repo, _ = build(
        AwayMode(active_date=date(2026, 9, 8)), now=AFTER_MIDNIGHT
    )

    state = await service.get_state()

    assert state.enabled is False
    # Still stored: nothing had to run at midnight to make the answer correct.
    assert repo.mode is not None
    assert repo.mode.active_date == date(2026, 9, 8)


async def test_a_finished_run_is_mailed_while_away_mode_is_on() -> None:
    service, _, mailer = build(AwayMode(active_date=date(2026, 9, 8)))

    await service.on_run_completed(4321)

    assert mailer.calls == [4321]


async def test_a_finished_run_is_not_mailed_while_away_mode_is_off() -> None:
    service, _, mailer = build()

    await service.on_run_completed(4321)

    assert mailer.calls == []


async def test_a_run_after_midnight_is_not_mailed_on_yesterdays_switch() -> None:
    service, _, mailer = build(
        AwayMode(active_date=date(2026, 9, 8)), now=AFTER_MIDNIGHT
    )

    await service.on_run_completed(4321)

    assert mailer.calls == []


async def test_a_mail_failure_never_escapes_into_the_finished_run() -> None:
    service, _, _ = build(
        AwayMode(active_date=date(2026, 9, 8)), mailer=FakeMailer(raises=True)
    )

    # The run already succeeded; an unsendable email may not undo that.
    await service.on_run_completed(4321)


async def test_a_refused_delivery_is_tolerated_too() -> None:
    mailer = FakeMailer(delivered=False)
    service, _, _ = build(AwayMode(active_date=date(2026, 9, 8)), mailer=mailer)

    await service.on_run_completed(4321)

    assert mailer.calls == [4321]


@pytest.mark.parametrize("enabled", [True, False])
async def test_setting_the_switch_is_idempotent(enabled: bool) -> None:
    service, repo, _ = build()

    await service.set_enabled(enabled)
    first = repo.mode
    await service.set_enabled(enabled)

    assert repo.mode is not None
    assert first is not None
    assert repo.mode.active_date == first.active_date
