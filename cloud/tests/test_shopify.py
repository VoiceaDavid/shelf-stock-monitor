"""Shopify client tests against a fake Shopify (httpx.MockTransport)."""
import json

import httpx
import pytest

from app.config import Settings
from app.models import Product
from app.shopify import ShopifyClient, ShopifyError
from app.stock import handle_event
from app.sync import sync_position


class FakeShopify:
    def __init__(self, available=10, stale_once=False):
        self.available = available
        self.stale_once = stale_once
        self.token_requests = 0
        self.mutations = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/oauth/access_token":
            self.token_requests += 1
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(200, json={"access_token": "tok", "scope": "write_inventory", "expires_in": 86399})
        assert request.headers["X-Shopify-Access-Token"] == "tok"
        body = json.loads(request.content)
        q = body["query"]
        if "locations" in q:
            return httpx.Response(200, json={"data": {"locations": {"nodes": [{"id": "gid://shopify/Location/1", "name": "Shop"}]}}})
        if "inventoryLevel" in q:
            return httpx.Response(200, json={"data": {"inventoryItem": {"inventoryLevel": {
                "quantities": [{"name": "available", "quantity": self.available}]}}}})
        if "inventorySetQuantities" in q:
            assert "@idempotent(key: $key)" in q and body["variables"]["key"]
            item = body["variables"]["input"]["quantities"][0]
            self.mutations.append(item)
            if self.stale_once:
                self.stale_once = False
                self.available += 1  # someone changed it meanwhile
                return httpx.Response(200, json={"data": {"inventorySetQuantities": {
                    "inventoryAdjustmentGroup": None,
                    "userErrors": [{"field": ["input"], "message": "stale", "code": "CHANGE_FROM_QUANTITY_STALE"}]}}})
            assert item["changeFromQuantity"] == self.available
            self.available = item["quantity"]
            return httpx.Response(200, json={"data": {"inventorySetQuantities": {
                "inventoryAdjustmentGroup": {"id": "gid://shopify/InventoryAdjustmentGroup/1"}, "userErrors": []}}})
        raise AssertionError(q)


def client(fake):
    s = Settings(shopify_shop="demo.myshopify.com", shopify_client_id="id", shopify_client_secret="secret",
                 shopify_dry_run=False, database_url="sqlite:///./test.db")
    return ShopifyClient(s, transport=httpx.MockTransport(fake))


def test_set_available_uses_compare_and_set():
    fake = FakeShopify(available=10)
    c = client(fake)
    c.set_available("gid://shopify/InventoryItem/1", 6, "ref")
    assert fake.available == 6
    assert fake.mutations[0]["changeFromQuantity"] == 10
    c.set_available("gid://shopify/InventoryItem/1", 5, "ref")
    assert fake.token_requests == 1  # token is cached


def test_same_value_is_not_sent_again():
    fake = FakeShopify(available=6)
    client(fake).set_available("gid://shopify/InventoryItem/1", 6, "ref")
    assert fake.mutations == []


def test_stale_value_is_retried():
    fake = FakeShopify(available=10, stale_once=True)
    client(fake).set_available("gid://shopify/InventoryItem/1", 4, "ref")
    assert fake.available == 4
    assert len(fake.mutations) == 2


def test_token_failure_raises():
    def deny(request):
        return httpx.Response(400, json={"error": "shop_not_permitted"})
    with pytest.raises(ShopifyError):
        client(deny).set_available("gid://shopify/InventoryItem/1", 4, "ref")


def test_sync_position_end_to_end(db):
    """Shelf event -> database -> Shopify available quantity (US-03)."""
    fake = FakeShopify(available=10)
    pos = handle_event(db, "shelf-01", {"pos": 1, "old": 10, "new": 7, "seq": 1})
    db.add(Product(variant_id="v1", inventory_item_id="gid://shopify/InventoryItem/1", title="Tea box",
                   threshold=3, position_id=pos.id))
    db.commit()
    assert sync_position(db, client(fake), pos.id) == "synced"
    assert fake.available == 7
    assert db.get(Product, 1).last_synced_count == 7


def test_unverified_position_is_not_pushed(db):
    fake = FakeShopify(available=10)
    pos = handle_event(db, "shelf-01", {"pos": 1, "old": 10, "new": 7, "seq": 1, "ok": False})
    db.add(Product(variant_id="v1", inventory_item_id="i1", title="Tea", position_id=pos.id))
    db.commit()
    assert sync_position(db, client(fake), pos.id).startswith("skipped")
    assert fake.mutations == []


def test_unlinked_position_is_not_pushed(db):
    pos = handle_event(db, "shelf-01", {"pos": 1, "old": 10, "new": 7, "seq": 1})
    assert sync_position(db, client(FakeShopify()), pos.id) == "not linked"
