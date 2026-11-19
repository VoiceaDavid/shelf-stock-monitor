"""Owner web page tests (US-04 link product, US-06 see counts)."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import DEMO_VARIANTS, app
from app.models import Product
from app.stock import handle_event


def form_value(v):
    return f"{v.id}|{v.inventory_item_id}|{v.title}"


def test_dashboard_shows_position_and_count(db):
    handle_event(db, "shelf-01", {"pos": 1, "old": 5, "new": 4, "seq": 1})
    with TestClient(app) as c:
        r = c.get("/")
    assert r.status_code == 200
    assert "shelf-01 / P1" in r.text
    assert ">4<" in r.text


def test_link_position_to_product_and_threshold(db):
    pos = handle_event(db, "shelf-01", {"pos": 1, "old": 5, "new": 4, "seq": 1})
    with TestClient(app) as c:
        assert "Chocolate bar 100 g" in c.get(f"/positions/{pos.id}/link").text
        r = c.post(f"/positions/{pos.id}/link", data={"variant": form_value(DEMO_VARIANTS[0]), "threshold": 3})
        assert r.status_code == 200
        api = c.get("/api/positions").json()
    assert api[0]["product"] == "Chocolate bar 100 g"
    assert api[0]["threshold"] == 3


def test_one_product_cannot_be_linked_to_two_positions(db):
    p1 = handle_event(db, "shelf-01", {"pos": 1, "old": 5, "new": 4, "seq": 1})
    p2 = handle_event(db, "shelf-01", {"pos": 2, "old": 5, "new": 4, "seq": 2})
    v = form_value(DEMO_VARIANTS[0])
    with TestClient(app) as c:
        c.post(f"/positions/{p1.id}/link", data={"variant": v, "threshold": 3})
        r = c.post(f"/positions/{p2.id}/link", data={"variant": v, "threshold": 3})
    assert "already linked" in r.text
    db.expire_all()
    assert db.scalar(select(Product)).position_id == p1.id


def test_relinking_a_position_frees_the_old_product(db):
    pos = handle_event(db, "shelf-01", {"pos": 1, "old": 5, "new": 4, "seq": 1})
    with TestClient(app) as c:
        c.post(f"/positions/{pos.id}/link", data={"variant": form_value(DEMO_VARIANTS[0]), "threshold": 3})
        c.post(f"/positions/{pos.id}/link", data={"variant": form_value(DEMO_VARIANTS[1]), "threshold": 2})
    db.expire_all()
    linked = {p.title: p.position_id for p in db.scalars(select(Product))}
    assert linked == {"Chocolate bar 100 g": None, "Biscuits 150 g": pos.id}


def test_calibrate_without_broker_shows_message(db):
    pos = handle_event(db, "shelf-01", {"pos": 1, "old": 5, "new": 4, "seq": 1})
    with TestClient(app) as c:
        r = c.post(f"/positions/{pos.id}/calibrate", data={"items": 10})
    assert "Could not reach the MQTT broker" in r.text


def test_health():
    with TestClient(app) as c:
        assert c.get("/health").json() == {"status": "ok"}
