"""The operator's watchlist: what it accepts, and how it reads back."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.business.watchlist import WatchlistItem, WatchlistService, normalize_symbol
from backend.business.watchlist.service import (
    StockNameUnavailableError,
    WatchlistAlreadyFollowedError,
)
from backend.infra.repositories import WatchlistRepository


class FakeNames:
    """Stands in for the quote provider that names a symbol."""

    def __init__(self, names: dict[str, str] | None = None) -> None:
        # `or` would treat an empty mapping as "use the default", which is
        # exactly the case a test needs to express.
        default = {"600519.SH": "贵州茅台", "000001.SZ": "平安银行"}
        self.names = default if names is None else names
        self.asked: list[str] = []

    async def name_for(self, symbol: str) -> str | None:
        self.asked.append(symbol)
        return self.names.get(symbol)


def _service(session, names: FakeNames | None = None) -> WatchlistService:
    return WatchlistService(
        WatchlistRepository(session),
        names=names or FakeNames(),
        committer=session,
    )


@pytest.mark.parametrize(
    ("written", "stored"),
    [
        ("600519", "600519.SH"),
        ("600519.SH", "600519.SH"),
        ("600519.sh", "600519.SH"),
        (" 000001 ", "000001.SZ"),
        ("300750", "300750.SZ"),
    ],
)
def test_a_code_is_stored_one_way_however_it_is_typed(
    written: str, stored: str
) -> None:
    """The exchange suffix is derived, so the uniqueness constraint means what
    it says."""

    assert normalize_symbol(written) == stored


@pytest.mark.parametrize(
    "written",
    ["60051", "abcdef", "999999", "600519.SZ", ""],
)
def test_codes_that_name_no_a_share_are_refused(written: str) -> None:
    with pytest.raises(ValueError):
        normalize_symbol(written)


@pytest.mark.asyncio
async def test_following_a_company_names_it_from_live_data(session) -> None:
    names = FakeNames()
    service = _service(session, names)

    item = await service.add("600519")

    assert item.symbol == "600519.SH"
    assert item.name == "贵州茅台"
    assert names.asked == ["600519.SH"]


@pytest.mark.asyncio
async def test_the_same_company_cannot_be_followed_twice(session) -> None:
    service = _service(session)
    await service.add("600519")

    # Typed differently, but the same company.
    with pytest.raises(WatchlistAlreadyFollowedError):
        await service.add("600519.SH")


@pytest.mark.asyncio
async def test_a_code_with_no_name_is_not_stored(session) -> None:
    """The lookup is the only check available that a code names a real
    company, so a nameless row would hide a typo until a run tried to use it."""

    service = _service(session, FakeNames(names={}))

    with pytest.raises(StockNameUnavailableError):
        await service.add("600519")

    assert await service.list_items() == []


@pytest.mark.asyncio
async def test_the_newest_addition_comes_first(session) -> None:
    repository = WatchlistRepository(session)
    now = datetime.now(tz=UTC)
    for offset, symbol in enumerate(("600519", "000001", "300750")):
        await repository.add(
            WatchlistItem(
                id=0,
                symbol=symbol,
                name="x",
                created_at=now - timedelta(days=offset),
                updated_at=now,
            )
        )
    await session.commit()

    items = await _service(session).list_items()

    assert [item.symbol for item in items] == [
        "600519.SH",
        "000001.SZ",
        "300750.SZ",
    ]


@pytest.mark.asyncio
async def test_unfollowing_removes_only_that_company(session) -> None:
    service = _service(session)
    first = await service.add("600519")
    await service.add("000001")

    assert await service.delete(first.id) is True
    assert [item.symbol for item in await service.list_items()] == ["000001.SZ"]


@pytest.mark.asyncio
async def test_unfollowing_something_absent_reports_it(session) -> None:
    assert await _service(session).delete(9999) is False


@pytest.mark.asyncio
async def test_the_list_stops_at_ten(session) -> None:
    """The ceiling bounds what a run screens, not just how tidy the page is."""

    from backend.business.watchlist import MAX_FOLLOWED
    from backend.business.watchlist.service import WatchlistFullError

    codes = [
        "600519", "000001", "300750", "601318", "600036",
        "000858", "002594", "601888", "600276", "300059",
        "600030",
    ]
    names = FakeNames(
        names={normalize_symbol(code): f"公司{i}" for i, code in enumerate(codes)}
    )
    service = _service(session, names)

    for code in codes[:MAX_FOLLOWED]:
        await service.add(code)

    with pytest.raises(WatchlistFullError, match=str(MAX_FOLLOWED)):
        await service.add(codes[MAX_FOLLOWED])

    assert len(await service.list_items()) == MAX_FOLLOWED


@pytest.mark.asyncio
async def test_a_full_list_does_not_call_the_quote_provider(session) -> None:
    """Nothing to gain from naming a company that cannot be stored."""

    from backend.business.watchlist.service import WatchlistFullError

    codes = [
        "600519", "000001", "300750", "601318", "600036",
        "000858", "002594", "601888", "600276", "300059",
    ]
    names = FakeNames(names={normalize_symbol(code): "x" for code in codes})
    service = _service(session, names)
    for code in codes:
        await service.add(code)
    names.asked.clear()

    with pytest.raises(WatchlistFullError):
        await service.add("600030")

    assert names.asked == []
