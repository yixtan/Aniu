"""Domain models for trade push notifications."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from backend.business.shared.trading import utc_now

MAX_CHANNEL_NAME_LENGTH = 64
MAX_BODY_TEMPLATE_LENGTH = 4000


class NotificationChannelKind(StrEnum):
    """Supported push transports.

    ``WEBHOOK`` posts a caller-defined JSON body to any URL, which covers 飞书 /
    钉钉 / n8n / self-hosted receivers. The other kinds wrap one vendor protocol
    each, because their request shape is not caller-configurable.
    """

    WEBHOOK = "webhook"
    SERVERCHAN = "serverchan"
    WECOM_BOT = "wecom_bot"


class TradeEventKind(StrEnum):
    """Trade lifecycle moments a channel can subscribe to.

    ``ORDER_PLACED`` and ``ORDER_CANCELLED`` are observed synchronously from the
    agent's write tools. ``ORDER_FILLED`` cannot be: an accepted limit order may
    never fill, so fills are detected by diffing the account order cache during
    a refresh.
    """

    ORDER_PLACED = "order_placed"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_FILLED = "order_filled"

    @property
    def label(self) -> str:
        return _EVENT_LABELS[self]


_EVENT_LABELS: dict[TradeEventKind, str] = {
    TradeEventKind.ORDER_PLACED: "已下单",
    TradeEventKind.ORDER_CANCELLED: "已撤单",
    TradeEventKind.ORDER_FILLED: "已成交",
}
DEFAULT_SUBSCRIBED_EVENTS: frozenset[TradeEventKind] = frozenset(TradeEventKind)


class TradeDirection(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def label(self) -> str:
        return "买入" if self is TradeDirection.BUY else "卖出"


def normalize_channel_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("channel name must not be empty")
    if len(name) > MAX_CHANNEL_NAME_LENGTH:
        raise ValueError(
            f"channel name must be at most {MAX_CHANNEL_NAME_LENGTH} characters"
        )
    return name


def normalize_body_template(value: str | None) -> str | None:
    if value is None:
        return None
    template = value.strip()
    if not template:
        return None
    if len(template) > MAX_BODY_TEMPLATE_LENGTH:
        raise ValueError(
            f"body template must be at most {MAX_BODY_TEMPLATE_LENGTH} characters"
        )
    return template


def normalize_subscribed_events(value: object) -> frozenset[TradeEventKind]:
    """Coerce persisted or client-supplied event names to a non-empty set."""

    if value is None:
        return DEFAULT_SUBSCRIBED_EVENTS
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise ValueError("subscribed_events must be a list of event names")
    events: set[TradeEventKind] = set()
    for item in value:
        if isinstance(item, TradeEventKind):
            events.add(item)
            continue
        try:
            events.add(TradeEventKind(str(item).strip()))
        except ValueError as exc:
            raise ValueError(f"unknown trade event: {item}") from exc
    if not events:
        raise ValueError("subscribed_events must not be empty")
    return frozenset(events)


@dataclass(frozen=True, slots=True)
class NotificationChannel:
    """One configured push target.

    The endpoint URL or vendor key is never held here; it lives in the encrypted
    secret store and is resolved only when a message is actually sent.
    """

    id: int
    name: str
    kind: NotificationChannelKind
    enabled: bool = True
    subscribed_events: frozenset[TradeEventKind] = DEFAULT_SUBSCRIBED_EVENTS
    body_template: str | None = None
    target_hint: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", normalize_channel_name(self.name))
        object.__setattr__(
            self, "body_template", normalize_body_template(self.body_template)
        )
        object.__setattr__(
            self,
            "subscribed_events",
            normalize_subscribed_events(self.subscribed_events),
        )
        if (
            self.body_template is not None
            and self.kind is not NotificationChannelKind.WEBHOOK
        ):
            raise ValueError("body_template is only supported by webhook channels")

    def wants(self, kind: TradeEventKind) -> bool:
        return self.enabled and kind in self.subscribed_events


@dataclass(frozen=True, slots=True)
class TradeNotificationEvent:
    """One trade lifecycle moment, ready to be rendered for any channel."""

    kind: TradeEventKind
    run_id: int | None = None
    stage_name: str | None = None
    tool_call_id: str | None = None
    instruction: str | None = None
    direction: TradeDirection | None = None
    stock_code: str | None = None
    stock_name: str | None = None
    price: float | None = None
    quantity: int | None = None
    order_id: str | None = None
    filled_quantity: int | None = None
    filled_price: float | None = None
    occurred_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.run_id is not None and self.run_id <= 0:
            raise ValueError("trade event run_id must be positive when provided")

    @property
    def title(self) -> str:
        subject = " ".join(
            part
            for part in (
                None if self.direction is None else self.direction.label,
                self.stock_code,
            )
            if part
        )
        if not subject:
            return f"Aniu {self.kind.label}"
        return f"Aniu {self.kind.label} · {subject}"

    def as_lines(self) -> tuple[tuple[str, str], ...]:
        """Ordered label/value pairs shared by every channel's rendering."""

        lines: list[tuple[str, str]] = [("事件", self.kind.label)]
        if self.direction is not None:
            lines.append(("方向", self.direction.label))
        if self.stock_code is not None:
            name = f" {self.stock_name}" if self.stock_name else ""
            lines.append(("标的", f"{self.stock_code}{name}"))
        if self.price is not None:
            lines.append(("委托价", f"{self.price:g}"))
        if self.quantity is not None:
            lines.append(("委托量", f"{self.quantity} 股"))
        if self.filled_quantity is not None:
            filled = f"{self.filled_quantity} 股"
            if self.filled_price is not None:
                filled += f" @ {self.filled_price:g}"
            lines.append(("成交", filled))
        if self.order_id is not None:
            lines.append(("委托号", self.order_id))
        if self.run_id is not None:
            lines.append(("运行", f"#{self.run_id}"))
        if self.instruction:
            lines.append(("指令", self.instruction))
        lines.append(
            ("时间", self.occurred_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"))
        )
        return tuple(lines)

    def as_text(self) -> str:
        return "\n".join(f"{label}：{value}" for label, value in self.as_lines())

    def as_markdown(self) -> str:
        body = "\n".join(f"- **{label}**：{value}" for label, value in self.as_lines())
        return f"### {self.title}\n\n{body}"

    def as_mapping(self) -> dict[str, object]:
        """Flat payload used by generic webhook body templates."""

        return {
            "event": self.kind.value,
            "event_label": self.kind.label,
            "title": self.title,
            "text": self.as_text(),
            "markdown": self.as_markdown(),
            "run_id": self.run_id,
            "stage_name": self.stage_name,
            "tool_call_id": self.tool_call_id,
            "instruction": self.instruction,
            "direction": None if self.direction is None else self.direction.value,
            "direction_label": (
                None if self.direction is None else self.direction.label
            ),
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "price": self.price,
            "quantity": self.quantity,
            "order_id": self.order_id,
            "filled_quantity": self.filled_quantity,
            "filled_price": self.filled_price,
            "occurred_at": self.occurred_at.isoformat(),
        }


__all__ = [
    "DEFAULT_SUBSCRIBED_EVENTS",
    "MAX_BODY_TEMPLATE_LENGTH",
    "MAX_CHANNEL_NAME_LENGTH",
    "NotificationChannel",
    "NotificationChannelKind",
    "TradeDirection",
    "TradeEventKind",
    "TradeNotificationEvent",
    "normalize_body_template",
    "normalize_channel_name",
    "normalize_subscribed_events",
]
