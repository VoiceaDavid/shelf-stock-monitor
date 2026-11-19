"""Database tables (offer, section 6). Sprint 1 creates the tables needed for
one position end to end; Notification is added in Sprint 2 with the alerts."""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Device(Base):
    """One ESP32 shelf node. Its id is its MQTT username."""

    __tablename__ = "device"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rssi: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    positions: Mapped[list["Position"]] = relationship(back_populates="device", order_by="Position.channel")


class Position(Base):
    """One scale (load cell) on a shelf."""

    __tablename__ = "position"
    __table_args__ = (UniqueConstraint("device_id", "channel"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id"))
    channel: Mapped[int] = mapped_column(Integer)  # 1..4 on the ESP32
    count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok / unverified / offline
    calibrated: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_per_item: Mapped[float | None]
    last_message: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    device: Mapped[Device] = relationship(back_populates="positions")
    product: Mapped["Product | None"] = relationship(back_populates="position", uselist=False)

    @property
    def label(self) -> str:
        return f"{self.device_id} / P{self.channel}"


class Product(Base):
    """A Shopify product variant linked to exactly one position (FR-04).
    position_id is unique, so this row is also the position-product mapping."""

    __tablename__ = "product"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_id: Mapped[str] = mapped_column(String(100), unique=True)
    inventory_item_id: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(255))
    threshold: Mapped[int] = mapped_column(Integer, default=3)  # FR-05
    position_id: Mapped[int | None] = mapped_column(ForeignKey("position.id"), unique=True)
    last_synced_count: Mapped[int | None] = mapped_column(Integer)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_error: Mapped[str | None] = mapped_column(String(500))

    position: Mapped[Position | None] = relationship(back_populates="product")


class StockChange(Base):
    """Every accepted count change. (device_id, seq) is unique, so a repeated
    MQTT message is stored only once."""

    __tablename__ = "stock_change"
    __table_args__ = (UniqueConstraint("device_id", "seq"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("position.id"))
    device_id: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int | None] = mapped_column(Integer)
    old_count: Mapped[int | None] = mapped_column(Integer)
    new_count: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(32))  # restock / taken / manual correction / online order
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    position: Mapped[Position] = relationship()
