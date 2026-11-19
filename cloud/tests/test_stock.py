from sqlalchemy import func, select

from app.models import Device, Position, StockChange
from app.stock import handle_event, handle_heartbeat


def test_event_creates_device_position_and_change(db):
    pos = handle_event(db, "shelf-01", {"pos": 1, "old": 5, "new": 4, "seq": 1, "ok": True})
    assert pos is not None
    assert pos.count == 4
    assert db.get(Device, "shelf-01").last_seen is not None
    change = db.scalar(select(StockChange))
    assert (change.old_count, change.new_count, change.reason) == (5, 4, "taken")


def test_restock_reason(db):
    handle_event(db, "shelf-01", {"pos": 1, "old": 2, "new": 9, "seq": 1})
    assert db.scalar(select(StockChange.reason)) == "restock"


def test_duplicate_event_is_ignored(db):
    msg = {"pos": 1, "old": 5, "new": 4, "seq": 7}
    assert handle_event(db, "shelf-01", msg) is not None
    assert handle_event(db, "shelf-01", msg) is None  # same seq again (MQTT redelivery)
    assert db.scalar(select(func.count()).select_from(StockChange)) == 1


def test_same_seq_from_other_device_is_kept(db):
    handle_event(db, "shelf-01", {"pos": 1, "old": 5, "new": 4, "seq": 1})
    handle_event(db, "shelf-02", {"pos": 1, "old": 5, "new": 4, "seq": 1})
    assert db.scalar(select(func.count()).select_from(StockChange)) == 2


def test_bad_event_is_rejected(db):
    assert handle_event(db, "shelf-01", {"pos": 1, "new": "x", "seq": 1}) is None
    assert handle_event(db, "shelf-01", {"new": 3}) is None


def test_unverified_flag(db):
    pos = handle_event(db, "shelf-01", {"pos": 1, "old": 3, "new": 3, "seq": 1, "ok": False})
    assert pos.status == "unverified"


def test_heartbeat_sets_initial_count_only_once(db):
    handle_heartbeat(db, "shelf-01", {"rssi": -60, "positions": [{"pos": 1, "count": 6, "calibrated": True}]})
    pos = db.scalar(select(Position))
    assert pos.count == 6 and pos.calibrated
    handle_event(db, "shelf-01", {"pos": 1, "old": 6, "new": 5, "seq": 1})
    handle_heartbeat(db, "shelf-01", {"rssi": -61, "positions": [{"pos": 1, "count": 6}]})
    assert db.scalar(select(Position)).count == 5  # events, not heartbeats, change the count
    assert db.get(Device, "shelf-01").rssi == -61
