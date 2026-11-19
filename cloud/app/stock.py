"""Handling of messages from the shelf devices (IF-01).

These functions take a database session and plain dicts, so they are easy to
unit-test without MQTT or Shopify."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Device, Position, StockChange, utcnow

log = logging.getLogger(__name__)


def get_or_create_position(db: Session, device_id: str, channel: int) -> Position:
    device = db.get(Device, device_id)
    if device is None:
        device = Device(id=device_id)
        db.add(device)
        db.flush()
    pos = db.scalar(select(Position).where(Position.device_id == device_id, Position.channel == channel))
    if pos is None:
        pos = Position(device_id=device_id, channel=channel)
        db.add(pos)
        db.flush()
    return pos


def handle_event(db: Session, device_id: str, msg: dict) -> Position | None:
    """A count change from the shelf. Returns the position if the online stock
    must be updated, or None for a duplicate / invalid message."""
    try:
        channel = int(msg["pos"])
        new = int(msg["new"])
        seq = int(msg["seq"])
    except (KeyError, TypeError, ValueError):
        log.warning("bad event from %s: %s", device_id, msg)
        return None
    old = msg.get("old")

    pos = get_or_create_position(db, device_id, channel)
    try:
        with db.begin_nested():
            db.add(
                StockChange(
                    position_id=pos.id,
                    device_id=device_id,
                    seq=seq,
                    old_count=old,
                    new_count=new,
                    reason="restock" if old is not None and new > old else "taken",
                )
            )
    except IntegrityError:
        log.info("duplicate event %s #%s ignored", device_id, seq)
        db.commit()
        return None

    pos.count = new
    pos.status = "ok" if msg.get("ok", True) else "unverified"
    pos.device.last_seen = utcnow()
    db.commit()
    return pos


def handle_heartbeat(db: Session, device_id: str, msg: dict) -> None:
    """Every 30 s: marks the device as seen and fills in counts of positions
    the cloud has not heard about yet (e.g. right after first start)."""
    for p in msg.get("positions", []):
        pos = get_or_create_position(db, device_id, int(p["pos"]))
        if pos.count is None:
            pos.count = int(p["count"])
        pos.calibrated = bool(p.get("calibrated", pos.calibrated))
        pos.status = "ok" if p.get("ok", True) else "unverified"
    device = db.get(Device, device_id) or Device(id=device_id)
    device.last_seen = utcnow()
    device.rssi = msg.get("rssi")
    db.add(device)
    db.commit()


def handle_calib(db: Session, device_id: str, msg: dict) -> None:
    pos = get_or_create_position(db, device_id, int(msg["pos"]))
    pos.last_message = f"{msg.get('action')}: {msg.get('msg')}"[:200]
    if msg.get("ok"):
        pos.raw_per_item = msg.get("raw_per_item")
        pos.calibrated = bool(pos.raw_per_item)
    db.commit()
