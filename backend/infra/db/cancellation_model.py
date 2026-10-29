"""Durable, monotonic system cancellation intents, independent of model plans."""

from sqlalchemy import JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.infra.db.base import Base, utc_now_iso


class ValuationCancellationModel(Base):
    __tablename__ = "valuation_cancellations"

    order_id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    stock_name: Mapped[str] = mapped_column(Text, nullable=False)
    order_price: Mapped[float | None]
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_at: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="REQUIRED")
    cause: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    triggered_by_run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_run_id: Mapped[int | None] = mapped_column(Integer)
    request_call_id: Mapped[str | None] = mapped_column(Text)
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_observed_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=utc_now_iso)
