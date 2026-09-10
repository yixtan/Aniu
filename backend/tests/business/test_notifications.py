"""Trade notification models, event extraction, fill diffing and dispatch."""

from __future__ import annotations

from dataclasses import replace

import pytest

from backend.business.notifications import (
    CreateChannelCommand,
    DeliveryStatus,
    NotificationChannel,
    NotificationChannelKind,
    NotificationDelivery,
    NotificationEvent,
    NotificationEventKind,
    NotificationService,
    OrderFillObservation,
    TradeDirection,
    UpdateChannelCommand,
    detect_fill_events,
    mask_secret,
    trade_event_from_tool_payload,
)
from backend.business.shared import NotificationChannelNotFoundError


def _trade_payload(
    *,
    tool_name: str = "trade",
    status: str = "ok",
    instruction: str = "买入 600519 1700 100",
    content: object | None = None,
) -> dict[str, object]:
    return {
        "tool_call_id": "call-1",
        "tool_name": tool_name,
        "status": status,
        "arguments": {"instruction": instruction},
        "content": {"orderId": "26215460"} if content is None else content,
    }


class FakeSender:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[int, NotificationEventKind]] = []
        self.events: list[NotificationEvent] = []
        self._fail = fail

    async def send(
        self,
        *,
        channel: NotificationChannel,
        secret: str,
        event: NotificationEvent,
    ) -> None:
        del secret
        if self._fail:
            raise RuntimeError("upstream refused")
        self.sent.append((channel.id, event.kind))
        self.events.append(event)


class FakeChannelRepo:
    def __init__(self, channels: list[NotificationChannel] | None = None) -> None:
        self.channels = list(channels or [])
        self.secrets: dict[int, str] = {
            channel.id: "https://example.test/hook" for channel in self.channels
        }
        self._next_id = max((c.id for c in self.channels), default=0) + 1

    async def list_channels(self) -> list[NotificationChannel]:
        return list(self.channels)

    async def get(self, channel_id: int) -> NotificationChannel | None:
        return next((c for c in self.channels if c.id == channel_id), None)

    async def create(self, **kwargs: object) -> NotificationChannel:
        secret = str(kwargs.pop("secret"))
        channel = NotificationChannel(id=self._next_id, **kwargs)  # type: ignore[arg-type]
        self._next_id += 1
        self.channels.append(channel)
        self.secrets[channel.id] = secret
        return channel

    async def update(
        self, channel_id: int, **kwargs: object
    ) -> NotificationChannel | None:
        existing = await self.get(channel_id)
        if existing is None:
            return None
        secret = kwargs.pop("secret")
        updated = NotificationChannel(
            id=channel_id,
            kind=existing.kind,
            **kwargs,  # type: ignore[arg-type]
        )
        self.channels = [updated if c.id == channel_id else c for c in self.channels]
        if secret is not None:
            self.secrets[channel_id] = str(secret)
        return updated

    async def delete(self, channel_id: int) -> bool:
        before = len(self.channels)
        self.channels = [c for c in self.channels if c.id != channel_id]
        return len(self.channels) != before

    async def get_secret(self, channel_id: int) -> str | None:
        return self.secrets.get(channel_id)


def _channel(
    channel_id: int = 1,
    *,
    enabled: bool = True,
    events: frozenset[NotificationEventKind] | None = None,
) -> NotificationChannel:
    return NotificationChannel(
        id=channel_id,
        name=f"通道{channel_id}",
        kind=NotificationChannelKind.WEBHOOK,
        enabled=enabled,
        subscribed_events=events or frozenset(NotificationEventKind),
    )


def test_trade_tool_payload_becomes_an_order_placed_event() -> None:
    event = trade_event_from_tool_payload(
        run_id=7, stage_name="Run", payload=_trade_payload()
    )

    assert event is not None
    assert event.kind is NotificationEventKind.ORDER_PLACED
    assert event.direction is TradeDirection.BUY
    assert event.stock_code == "600519"
    assert event.price == 1700.0
    assert event.quantity == 100
    assert event.order_id == "26215460"
    assert event.run_id == 7


def test_sell_instruction_and_nested_order_id_are_parsed() -> None:
    event = trade_event_from_tool_payload(
        run_id=1,
        stage_name="Run",
        payload=_trade_payload(
            instruction="卖出 000001.SZ 12.5 200",
            content={"data": {"orderId": "99"}},
        ),
    )

    assert event is not None
    assert event.direction is TradeDirection.SELL
    assert event.stock_code == "000001"
    assert event.quantity == 200
    assert event.order_id == "99"


def test_cancel_tool_payload_becomes_an_order_cancelled_event() -> None:
    event = trade_event_from_tool_payload(
        run_id=3,
        stage_name="Run",
        payload=_trade_payload(
            tool_name="cancel",
            instruction="撤单 262154600000047682 515880",
            content={"success": True},
        ),
    )

    assert event is not None
    assert event.kind is NotificationEventKind.ORDER_CANCELLED
    assert event.order_id == "262154600000047682"
    assert event.stock_code == "515880"


def test_cancel_all_notifies_without_order_details() -> None:
    event = trade_event_from_tool_payload(
        run_id=3,
        stage_name="Run",
        payload=_trade_payload(
            tool_name="cancel",
            instruction="一键撤单",
            content={"success": True},
        ),
    )

    assert event is not None
    assert event.kind is NotificationEventKind.ORDER_CANCELLED
    assert event.order_id is None
    assert "已撤单" in event.title


@pytest.mark.parametrize(
    "payload",
    [
        _trade_payload(status="error"),
        _trade_payload(tool_name="query_portfolio"),
        _trade_payload(content={"code": "500", "success": False}),
    ],
)
def test_non_trade_outcomes_do_not_notify(payload: dict[str, object]) -> None:
    assert (
        trade_event_from_tool_payload(run_id=1, stage_name="Run", payload=payload)
        is None
    )


def _observation(
    order_id: str = "A1",
    *,
    filled: int = 0,
    quantity: int = 100,
) -> OrderFillObservation:
    return OrderFillObservation(
        order_id=order_id,
        symbol="600519",
        stock_name="贵州茅台",
        direction="BUY",
        status="FILLED" if filled >= quantity else "PARTIAL",
        quantity=quantity,
        filled_quantity=filled,
        order_price=1700.0,
        filled_price=1699.0 if filled else None,
    )


def test_first_fill_notifies_and_sets_the_watermark() -> None:
    result = detect_fill_events([_observation(filled=100)], {})

    assert len(result.events) == 1
    assert result.events[0].kind is NotificationEventKind.ORDER_FILLED
    assert result.events[0].filled_quantity == 100
    assert result.watermarks == {"A1": 100}


def test_repeated_refresh_does_not_resend_the_same_fill() -> None:
    result = detect_fill_events([_observation(filled=100)], {"A1": 100})

    assert result.events == ()
    assert result.watermarks == {"A1": 100}


def test_partial_fill_reports_only_the_newly_filled_quantity() -> None:
    result = detect_fill_events([_observation(filled=300, quantity=500)], {"A1": 100})

    assert len(result.events) == 1
    assert result.events[0].filled_quantity == 200
    assert result.watermarks == {"A1": 300}


def test_cold_start_records_a_baseline_without_announcing_history() -> None:
    """The first refresh after install must not replay old fills."""

    result = detect_fill_events(
        [_observation("A1", filled=100), _observation("B2", filled=500, quantity=500)],
        {},
        cold_start=True,
    )

    assert result.events == ()
    assert result.watermarks == {"A1": 100, "B2": 500}


def test_fills_after_the_cold_start_baseline_do_notify() -> None:
    baseline = detect_fill_events(
        [_observation("A1", filled=100, quantity=500)], {}, cold_start=True
    )

    result = detect_fill_events(
        [_observation("A1", filled=300, quantity=500)], baseline.watermarks
    )

    assert len(result.events) == 1
    assert result.events[0].filled_quantity == 200


def test_unfilled_orders_never_notify() -> None:
    assert detect_fill_events([_observation(filled=0)], {}).events == ()


def test_orders_absent_from_the_refresh_are_pruned() -> None:
    result = detect_fill_events([_observation("B2", filled=100)], {"A1": 100})

    assert "A1" not in result.watermarks


@pytest.mark.parametrize(
    ("secret", "expected"),
    [
        (
            "https://qyapi.weixin.qq.com/send?key=abcd1234",
            "https://qyapi.weixin.qq.com/…1234",
        ),
        ("SCT123456abcd", "…abcd"),
        ("xy", "…xy"),
    ],
)
def test_mask_secret_never_reveals_a_usable_credential(
    secret: str, expected: str
) -> None:
    assert mask_secret(secret) == expected


@pytest.mark.asyncio
async def test_publish_only_reaches_subscribed_enabled_channels() -> None:
    repo = FakeChannelRepo(
        [
            _channel(1, events=frozenset({NotificationEventKind.ORDER_PLACED})),
            _channel(2, events=frozenset({NotificationEventKind.ORDER_FILLED})),
            _channel(3, enabled=False),
        ]
    )
    sender = FakeSender()
    service = NotificationService(channel_repo=repo, sender=sender)

    delivered = await service.publish(
        NotificationEvent(kind=NotificationEventKind.ORDER_PLACED, run_id=1)
    )

    assert delivered == 1
    assert sender.sent == [(1, NotificationEventKind.ORDER_PLACED)]


@pytest.mark.asyncio
async def test_publish_swallows_delivery_failures() -> None:
    service = NotificationService(
        channel_repo=FakeChannelRepo([_channel(1)]), sender=FakeSender(fail=True)
    )

    assert (
        await service.publish(
            NotificationEvent(kind=NotificationEventKind.ORDER_PLACED, run_id=1)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_create_channel_stores_a_masked_hint_only() -> None:
    repo = FakeChannelRepo()
    service = NotificationService(channel_repo=repo, sender=FakeSender())

    dto = await service.create_channel(
        CreateChannelCommand(
            name="我的机器人",
            kind=NotificationChannelKind.WECOM_BOT,
            secret="abcdef123456",
            subscribed_events=["order_placed"],
        )
    )

    assert dto.target_hint == "…3456"
    assert dto.subscribed_events == (NotificationEventKind.ORDER_PLACED,)
    assert repo.secrets[dto.id] == "abcdef123456"


@pytest.mark.asyncio
async def test_blank_secret_on_update_keeps_the_stored_endpoint() -> None:
    repo = FakeChannelRepo([_channel(1)])
    repo.secrets[1] = "https://example.test/original"
    service = NotificationService(channel_repo=repo, sender=FakeSender())

    await service.update_channel(
        UpdateChannelCommand(channel_id=1, name="改名了", secret="")
    )

    assert repo.secrets[1] == "https://example.test/original"


@pytest.mark.asyncio
async def test_updating_a_missing_channel_raises_not_found() -> None:
    service = NotificationService(channel_repo=FakeChannelRepo(), sender=FakeSender())

    with pytest.raises(NotificationChannelNotFoundError):
        await service.update_channel(UpdateChannelCommand(channel_id=404, name="x"))


@pytest.mark.asyncio
async def test_test_send_reports_a_failed_delivery() -> None:
    service = NotificationService(
        channel_repo=FakeChannelRepo([_channel(1)]), sender=FakeSender(fail=True)
    )

    result = await service.send_test(1)

    assert result.delivered is False
    assert "发送失败" in result.message


def test_body_template_is_rejected_for_vendor_channels() -> None:
    with pytest.raises(ValueError, match="body_template"):
        NotificationChannel(
            id=1,
            name="server酱",
            kind=NotificationChannelKind.SERVERCHAN,
            body_template='{"a": 1}',
        )


class FakeDeliveryRepo:
    def __init__(self) -> None:
        self.rows: list[NotificationDelivery] = []

    async def append(self, delivery: NotificationDelivery) -> NotificationDelivery:
        stored = replace(delivery, id=len(self.rows) + 1)
        self.rows.append(stored)
        return stored

    async def list_page(self, *, limit: int, offset: int):
        return list(reversed(self.rows))[offset : offset + limit]

    async def count(self) -> int:
        return len(self.rows)


def test_run_failure_event_reads_as_a_failure_not_a_trade() -> None:
    event = NotificationEvent(
        kind=NotificationEventKind.RUN_FAILED,
        run_id=128,
        stage_name="Run",
        failure_reason="MX request failed: 余额不足",
    )

    assert event.title == "Aniu 运行失败 · 运行 #128"
    text = event.as_text()
    assert "失败阶段：Run" in text
    assert "原因：MX request failed: 余额不足" in text
    assert event.as_mapping()["event"] == "run_failed"


def test_a_long_failure_reason_is_truncated() -> None:
    event = NotificationEvent(
        kind=NotificationEventKind.RUN_FAILED, run_id=1, failure_reason="x" * 900
    )

    assert event.failure_reason is not None
    assert len(event.failure_reason) == 500
    assert event.failure_reason.endswith("…")


def test_a_blank_failure_reason_is_dropped() -> None:
    assert (
        NotificationEvent(
            kind=NotificationEventKind.RUN_FAILED, run_id=1, failure_reason="   "
        ).failure_reason
        is None
    )


@pytest.mark.asyncio
async def test_a_successful_push_is_recorded_in_history() -> None:
    deliveries = FakeDeliveryRepo()
    service = NotificationService(
        channel_repo=FakeChannelRepo([_channel(1)]),
        sender=FakeSender(),
        delivery_repo=deliveries,
    )

    await service.publish(
        NotificationEvent(kind=NotificationEventKind.ORDER_PLACED, run_id=1)
    )

    assert len(deliveries.rows) == 1
    row = deliveries.rows[0]
    assert row.status is DeliveryStatus.DELIVERED
    assert row.channel_name == "通道1"
    assert row.event_kind is NotificationEventKind.ORDER_PLACED
    assert row.error_message is None
    assert row.is_test is False


@pytest.mark.asyncio
async def test_a_failed_push_is_recorded_with_its_error() -> None:
    deliveries = FakeDeliveryRepo()
    service = NotificationService(
        channel_repo=FakeChannelRepo([_channel(1)]),
        sender=FakeSender(fail=True),
        delivery_repo=deliveries,
    )

    await service.publish(
        NotificationEvent(kind=NotificationEventKind.RUN_FAILED, run_id=9)
    )

    assert len(deliveries.rows) == 1
    assert deliveries.rows[0].status is DeliveryStatus.FAILED
    assert "upstream refused" in (deliveries.rows[0].error_message or "")


@pytest.mark.asyncio
async def test_a_test_send_is_recorded_and_flagged_as_a_test() -> None:
    deliveries = FakeDeliveryRepo()
    service = NotificationService(
        channel_repo=FakeChannelRepo([_channel(1)]),
        sender=FakeSender(),
        delivery_repo=deliveries,
    )

    await service.send_test(1)

    assert len(deliveries.rows) == 1
    assert deliveries.rows[0].is_test is True


@pytest.mark.asyncio
async def test_history_reads_back_newest_first_with_a_total() -> None:
    deliveries = FakeDeliveryRepo()
    service = NotificationService(
        channel_repo=FakeChannelRepo([_channel(1)]),
        sender=FakeSender(),
        delivery_repo=deliveries,
    )
    for _ in range(3):
        await service.publish(
            NotificationEvent(kind=NotificationEventKind.ORDER_PLACED, run_id=1)
        )

    page = await service.list_deliveries(limit=2, offset=0)

    assert page.total == 3
    assert [item.id for item in page.items] == [3, 2]


@pytest.mark.asyncio
async def test_history_failures_never_break_the_push() -> None:
    class ExplodingRepo(FakeDeliveryRepo):
        async def append(self, delivery: NotificationDelivery) -> NotificationDelivery:
            raise RuntimeError("history table is gone")

    service = NotificationService(
        channel_repo=FakeChannelRepo([_channel(1)]),
        sender=FakeSender(),
        delivery_repo=ExplodingRepo(),
    )

    assert (
        await service.publish(
            NotificationEvent(kind=NotificationEventKind.ORDER_PLACED, run_id=1)
        )
        == 1
    )


class RecordingNames:
    """Stands in for the quote lookup that names a code."""

    def __init__(self, name: str | None = "天孚通信", fail: bool = False) -> None:
        self.asked: list[str] = []
        self._name = name
        self._fail = fail

    async def name_for(self, symbol: str) -> str | None:
        self.asked.append(symbol)
        if self._fail:
            raise RuntimeError("quote provider is down")
        return self._name


def _placed(**overrides: object) -> NotificationEvent:
    fields: dict[str, object] = {
        "kind": NotificationEventKind.ORDER_PLACED,
        "stock_code": "300394",
        "direction": TradeDirection.BUY,
        "price": 268.0,
        "quantity": 200,
    }
    fields.update(overrides)
    return NotificationEvent(**fields)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_an_order_push_names_the_company_behind_the_code() -> None:
    """A fill is read off the order list and already carries the name; an order
    is read back from "买入 300394 268 200" and carries only the code."""

    sender = FakeSender()
    names = RecordingNames()
    service = NotificationService(
        channel_repo=FakeChannelRepo([_channel(1)]), sender=sender, names=names
    )

    await service.publish(_placed())

    assert names.asked == ["300394"]
    assert sender.events[-1].stock_name == "天孚通信"
    assert ("标的", "300394 天孚通信") in sender.events[-1].as_lines()


@pytest.mark.asyncio
async def test_an_event_that_already_names_the_company_is_not_looked_up() -> None:
    names = RecordingNames()
    service = NotificationService(
        channel_repo=FakeChannelRepo([_channel(1)]), sender=FakeSender(), names=names
    )

    await service.publish(
        NotificationEvent(
            kind=NotificationEventKind.ORDER_FILLED,
            stock_code="300394",
            stock_name="天孚通信",
        )
    )

    assert names.asked == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "names", [RecordingNames(name=None), RecordingNames(fail=True)]
)
async def test_a_name_that_cannot_be_resolved_still_sends_the_push(
    names: RecordingNames,
) -> None:
    """The company name is a nicety; what happened to the account is not."""

    sender = FakeSender()
    service = NotificationService(
        channel_repo=FakeChannelRepo([_channel(1)]), sender=sender, names=names
    )

    delivered = await service.publish(_placed())

    assert delivered == 1
    assert sender.events[-1].stock_name is None
    assert ("标的", "300394") in sender.events[-1].as_lines()


@pytest.mark.asyncio
async def test_without_a_lookup_wired_the_push_carries_the_code_alone() -> None:
    sender = FakeSender()
    service = NotificationService(
        channel_repo=FakeChannelRepo([_channel(1)]), sender=sender
    )

    assert await service.publish(_placed()) == 1
    assert sender.events[-1].stock_name is None


def test_the_title_carries_the_name_beside_the_code() -> None:
    """The title is the whole message on a lock screen."""

    assert _placed(stock_name="天孚通信").title == "Aniu 已下单 · 买入 300394 天孚通信"


def test_the_title_falls_back_to_the_code_when_no_name_resolved() -> None:
    assert _placed().title == "Aniu 已下单 · 买入 300394"
