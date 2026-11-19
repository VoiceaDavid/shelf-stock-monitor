"""Shopify Admin GraphQL client (IF-02).

Only documented Shopify interfaces are used:
  * client credentials grant (Dev Dashboard app) to get an access token
  * productVariants / locations / inventoryItem queries
  * inventorySetQuantities mutation, with changeFromQuantity (compare-and-set)
    and the @idempotent directive, both required since API version 2026-04.

We always send the full shelf count ("set to 6"), never "-1", so a repeated
message can never subtract twice.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass

import httpx

from .config import Settings, get_settings

log = logging.getLogger(__name__)


class ShopifyError(Exception):
    pass


@dataclass
class Variant:
    id: str
    title: str
    sku: str | None
    inventory_item_id: str
    tracked: bool


SET_QUANTITIES = """
mutation SetShelfStock($input: InventorySetQuantitiesInput!, $key: String!) {
  inventorySetQuantities(input: $input) @idempotent(key: $key) {
    inventoryAdjustmentGroup { id }
    userErrors { field message code }
  }
}
"""

GET_AVAILABLE = """
query Available($item: ID!, $location: ID!) {
  inventoryItem(id: $item) {
    inventoryLevel(locationId: $location) {
      quantities(names: ["available"]) { name quantity }
    }
  }
}
"""

LIST_VARIANTS = """
query Variants($cursor: String) {
  productVariants(first: 100, after: $cursor) {
    nodes { id displayName sku inventoryItem { id tracked } }
    pageInfo { hasNextPage endCursor }
  }
}
"""

FIRST_LOCATION = "{ locations(first: 1) { nodes { id name } } }"


class ShopifyClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None):
        self.s = settings or get_settings()
        self._http = httpx.Client(timeout=15, transport=transport)
        self._token: str | None = None
        self._token_expires = 0.0
        self._location_id: str | None = self.s.shopify_location_id or None
        self._lock = threading.Lock()

    # ---------- auth ----------
    def _access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_expires - 300:
                return self._token
            r = self._http.post(
                f"https://{self.s.shopify_shop}/admin/oauth/access_token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.s.shopify_client_id,
                    "client_secret": self.s.shopify_client_secret,
                },
            )
            if r.status_code != 200:
                raise ShopifyError(f"token request failed ({r.status_code}): {r.text[:200]}")
            body = r.json()
            self._token = body["access_token"]
            self._token_expires = time.time() + int(body.get("expires_in", 86399))
            return self._token

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        r = self._http.post(
            f"https://{self.s.shopify_shop}/admin/api/{self.s.shopify_api_version}/graphql.json",
            json={"query": query, "variables": variables or {}},
            headers={"X-Shopify-Access-Token": self._access_token()},
        )
        if r.status_code != 200:
            raise ShopifyError(f"HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        if body.get("errors"):
            raise ShopifyError(str(body["errors"])[:300])
        return body["data"]

    # ---------- reads ----------
    def location_id(self) -> str:
        if not self._location_id:
            nodes = self.graphql(FIRST_LOCATION)["locations"]["nodes"]
            if not nodes:
                raise ShopifyError("the store has no location")
            self._location_id = nodes[0]["id"]
        return self._location_id

    def list_variants(self) -> list[Variant]:
        out: list[Variant] = []
        cursor = None
        while True:
            page = self.graphql(LIST_VARIANTS, {"cursor": cursor})["productVariants"]
            for n in page["nodes"]:
                out.append(
                    Variant(
                        id=n["id"],
                        title=n["displayName"],
                        sku=n.get("sku"),
                        inventory_item_id=n["inventoryItem"]["id"],
                        tracked=bool(n["inventoryItem"]["tracked"]),
                    )
                )
            if not page["pageInfo"]["hasNextPage"]:
                return out
            cursor = page["pageInfo"]["endCursor"]

    def get_available(self, inventory_item_id: str) -> int:
        data = self.graphql(GET_AVAILABLE, {"item": inventory_item_id, "location": self.location_id()})
        level = (data.get("inventoryItem") or {}).get("inventoryLevel")
        if not level:
            raise ShopifyError("product is not stocked at this location (turn on 'Track quantity')")
        return int(level["quantities"][0]["quantity"])

    # ---------- write ----------
    def set_available(self, inventory_item_id: str, quantity: int, reference: str, attempts: int = 3) -> None:
        """Set the 'available' quantity. Reads the current value first and passes
        it as changeFromQuantity, so a concurrent change in Shopify is not
        overwritten blindly; on a stale value we read again and retry."""
        if self.s.shopify_dry_run:
            log.info("[dry run] would set %s to %d", inventory_item_id, quantity)
            return
        for attempt in range(1, attempts + 1):
            current = self.get_available(inventory_item_id)
            if current == quantity:
                return
            data = self.graphql(
                SET_QUANTITIES,
                {
                    "key": str(uuid.uuid4()),
                    "input": {
                        "name": "available",
                        "reason": "correction",
                        "referenceDocumentUri": reference,
                        "quantities": [
                            {
                                "inventoryItemId": inventory_item_id,
                                "locationId": self.location_id(),
                                "quantity": quantity,
                                "changeFromQuantity": current,
                            }
                        ],
                    },
                },
            )
            errors = data["inventorySetQuantities"]["userErrors"]
            if not errors:
                log.info("Shopify %s: %d -> %d", inventory_item_id, current, quantity)
                return
            if any(e.get("code") == "CHANGE_FROM_QUANTITY_STALE" for e in errors) and attempt < attempts:
                continue
            raise ShopifyError("; ".join(e["message"] for e in errors))
