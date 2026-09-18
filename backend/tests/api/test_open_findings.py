"""Raising, listing and closing an unanswered objection."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

FINDING = "五笔买入限价单全属 AI 硬件链，成交条件与风险条件相同"
TEST = "说明在什么行情下它们会分批而非同时成交，或把敞口拆到不相关的主线上"


@pytest.mark.asyncio
async def test_a_finding_must_arrive_with_what_would_settle_it(
    api_client: AsyncClient,
) -> None:
    """The gate against 「是否考虑了流动性风险」.

    Every open finding is answered in every run, so one nobody can settle is
    paid for forever. It is refused at the door rather than argued about.
    """

    response = await api_client.post(
        "/api/aniu/open-findings",
        json={"finding": FINDING, "resolution_test": ""},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_raising_one_puts_it_on_the_open_list(api_client: AsyncClient) -> None:
    created = await api_client.post(
        "/api/aniu/open-findings",
        json={"finding": FINDING, "resolution_test": TEST},
    )

    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "OPEN"
    assert body["times_disputed"] == 0
    assert body["closed_at"] is None

    listed = (await api_client.get("/api/aniu/open-findings")).json()
    assert [item["finding"] for item in listed] == [FINDING]


@pytest.mark.asyncio
async def test_closing_one_takes_it_off_the_list_a_run_sees(
    api_client: AsyncClient,
) -> None:
    """Closing is a person's act, which is why it has its own endpoint.

    A run may say where it stands; letting it also declare the matter settled
    would let it write 「经复核，该顾虑不成立」 and move on.
    """

    finding_id = (
        await api_client.post(
            "/api/aniu/open-findings",
            json={"finding": FINDING, "resolution_test": TEST},
        )
    ).json()["finding_id"]

    closed = await api_client.post(
        f"/api/aniu/open-findings/{finding_id}/close",
        json={"outcome": "MET", "note": "浅档三笔分批成交，了结条件已由行情满足。"},
    )

    assert closed.status_code == 200
    assert closed.json()["status"] == "CLOSED"
    assert closed.json()["closed_at"] is not None


@pytest.mark.asyncio
async def test_closing_something_that_is_not_there_says_so(
    api_client: AsyncClient,
) -> None:
    missing = await api_client.post(
        "/api/aniu/open-findings/999/close",
        json={"outcome": "MET", "note": "了结。"},
    )

    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_closing_without_a_reason_is_refused(api_client: AsyncClient) -> None:
    """Same gate as the resolution test, at the other end of the finding."""

    raised = await api_client.post(
        "/api/aniu/open-findings",
        json={"finding": FINDING, "resolution_test": TEST},
    )
    finding_id = raised.json()["finding_id"]

    response = await api_client.post(
        f"/api/aniu/open-findings/{finding_id}/close",
        json={"outcome": "MET", "note": ""},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_closed_finding_carries_why_it_closed(
    api_client: AsyncClient,
) -> None:
    raised = await api_client.post(
        "/api/aniu/open-findings",
        json={"finding": FINDING, "resolution_test": TEST},
    )
    finding_id = raised.json()["finding_id"]

    body = (
        await api_client.post(
            f"/api/aniu/open-findings/{finding_id}/close",
            json={
                "outcome": "MET",
                "note": "浅档三笔分批成交、深档未触发，了结条件由行情满足。",
            },
        )
    ).json()

    assert body["status"] == "CLOSED"
    assert body["closing_note"].startswith("浅档三笔")


@pytest.mark.asyncio
async def test_a_fresh_finding_is_not_waiting_on_anyone(
    api_client: AsyncClient,
) -> None:
    """Never null: the page reads this to decide whether to badge the row."""

    body = (
        await api_client.post(
            "/api/aniu/open-findings",
            json={"finding": FINDING, "resolution_test": TEST},
        )
    ).json()

    assert body["settlement_proposed"] is False
    assert body["closing_note"] == ""


@pytest.mark.asyncio
async def test_closing_must_say_which_of_the_two_endings(
    api_client: AsyncClient,
) -> None:
    """Free text alone made the page ask an open question and left the person
    with a blank box; naming the two endings is what made it answerable."""

    raised = await api_client.post(
        "/api/aniu/open-findings",
        json={"finding": FINDING, "resolution_test": TEST},
    )
    finding_id = raised.json()["finding_id"]

    response = await api_client.post(
        f"/api/aniu/open-findings/{finding_id}/close",
        json={"note": "关掉了。"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_withdrawn_finding_is_not_a_settled_one(
    api_client: AsyncClient,
) -> None:
    raised = await api_client.post(
        "/api/aniu/open-findings",
        json={"finding": FINDING, "resolution_test": TEST},
    )
    finding_id = raised.json()["finding_id"]

    body = (
        await api_client.post(
            f"/api/aniu/open-findings/{finding_id}/close",
            json={"outcome": "WITHDRAWN", "note": "了结条件里的 115 口径是误引。"},
        )
    ).json()

    assert body["closing_outcome"] == "WITHDRAWN"
    assert body["closing_note"].startswith("了结条件里的")
