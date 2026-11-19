"""Background worker that pushes shelf counts to Shopify.

Positions to sync are put on a queue. The worker always reads the newest
count from the database, so if several changes arrive quickly only the latest
value is sent ("newest count wins")."""
from __future__ import annotations

import logging
import queue
import threading

from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Position, utcnow
from .shopify import ShopifyClient, ShopifyError

log = logging.getLogger(__name__)


def sync_position(db: Session, shop: ShopifyClient, position_id: int) -> str:
    """Push one position's count to its linked product. Returns a short status."""
    pos = db.get(Position, position_id)
    if pos is None or pos.product is None:
        return "not linked"
    if pos.count is None:
        return "no count yet"
    if pos.status != "ok":
        return f"skipped ({pos.status})"  # never push a doubtful number (AC-15)
    product = pos.product
    try:
        shop.set_available(
            product.inventory_item_id,
            pos.count,
            reference=f"shelf-stock-monitor://position/{pos.id}",
        )
    except (ShopifyError, OSError) as e:
        product.last_sync_error = str(e)[:500]
        db.commit()
        log.error("sync of %s failed: %s", pos.label, e)
        return "error"
    product.last_synced_count = pos.count
    product.last_synced_at = utcnow()
    product.last_sync_error = None
    db.commit()
    return "synced"


class SyncWorker:
    def __init__(self, shop: ShopifyClient):
        self.shop = shop
        self.q: queue.Queue[int] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="shopify-sync", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def request(self, position_id: int) -> None:
        self.q.put(position_id)

    def _run(self) -> None:
        while True:
            position_id = self.q.get()
            # Drop queued duplicates of the same position: the newest count is read anyway.
            pending = {position_id}
            while not self.q.empty():
                pending.add(self.q.get_nowait())
            for pid in pending:
                with SessionLocal() as db:
                    try:
                        sync_position(db, self.shop, pid)
                    except Exception:  # keep the worker alive
                        log.exception("unexpected sync failure")
