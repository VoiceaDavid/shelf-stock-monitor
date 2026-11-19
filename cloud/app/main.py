"""Shelf-Stock Monitor cloud service: FastAPI app with the owner web page
(IF-05), the MQTT listener (IF-01) and the Shopify sync worker (IF-02)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal, init_db
from .models import Device, Position, Product, StockChange
from .mqtt_worker import MqttWorker
from .shopify import ShopifyClient, ShopifyError, Variant
from .sync import SyncWorker, sync_position

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

DEMO_VARIANTS = [  # used only when SHOPIFY_DRY_RUN=true
    Variant("gid://shopify/ProductVariant/1", "Chocolate bar 100 g", "CHOC-100", "gid://shopify/InventoryItem/1", True),
    Variant("gid://shopify/ProductVariant/2", "Biscuits 150 g", "BISC-150", "gid://shopify/InventoryItem/2", True),
    Variant("gid://shopify/ProductVariant/3", "Tea box", "TEA-20", "gid://shopify/InventoryItem/3", True),
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    app.state.shop = ShopifyClient(settings)
    app.state.sync = SyncWorker(app.state.shop)
    app.state.sync.start()
    app.state.mqtt = None
    if settings.mqtt_enabled:
        app.state.mqtt = MqttWorker(settings, app.state.sync)
        app.state.mqtt.start()
    yield
    if app.state.mqtt:
        app.state.mqtt.stop()


app = FastAPI(title="Shelf-Stock Monitor", lifespan=lifespan)


def get_db():
    with SessionLocal() as db:
        yield db


def _position(db: Session, position_id: int) -> Position:
    pos = db.get(Position, position_id)
    if pos is None:
        raise HTTPException(404, "position not found")
    return pos


def _back(msg: str = "") -> RedirectResponse:
    return RedirectResponse(f"/?msg={msg}" if msg else "/", status_code=303)


# ---------- pages ----------
@app.get("/")
def dashboard(request: Request, msg: str = "", db: Session = Depends(get_db)):
    positions = db.scalars(select(Position).order_by(Position.device_id, Position.channel)).all()
    devices = db.scalars(select(Device).order_by(Device.id)).all()
    history = db.scalars(select(StockChange).order_by(StockChange.id.desc()).limit(15)).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"positions": positions, "devices": devices, "history": history, "msg": msg,
         "dry_run": get_settings().shopify_dry_run},
    )


@app.get("/positions/{position_id}/link")
def link_form(request: Request, position_id: int, db: Session = Depends(get_db)):
    pos = _position(db, position_id)
    error = ""
    variants: list[Variant] = []
    try:
        variants = DEMO_VARIANTS if get_settings().shopify_dry_run else request.app.state.shop.list_variants()
    except (ShopifyError, OSError) as e:
        error = f"Could not load products from Shopify: {e}"
    taken = {p.variant_id for p in db.scalars(select(Product).where(Product.position_id.is_not(None)))}
    return templates.TemplateResponse(
        request,
        "link.html",
        {"pos": pos, "variants": variants, "taken": taken, "error": error},
    )


@app.post("/positions/{position_id}/link")
def link_save(
    request: Request,
    position_id: int,
    variant: str = Form(...),
    threshold: int = Form(3),
    db: Session = Depends(get_db),
):
    """FR-04: one position <-> exactly one product variant. FR-05: threshold."""
    pos = _position(db, position_id)
    variant_id, inventory_item_id, title = variant.split("|", 2)
    if threshold < 0:
        raise HTTPException(400, "threshold must be 0 or more")

    other = db.scalar(select(Product).where(Product.variant_id == variant_id))
    if other and other.position_id not in (None, pos.id):
        return _back("That product is already linked to another position")
    if pos.product and pos.product.variant_id != variant_id:
        pos.product.position_id = None  # unlink the previous product
        db.flush()

    product = other or Product(variant_id=variant_id)
    product.inventory_item_id = inventory_item_id
    product.title = title
    product.threshold = threshold
    product.position_id = pos.id
    db.add(product)
    db.commit()
    request.app.state.sync.request(pos.id)  # push the current count right away
    return _back(f"Linked {pos.label} to {title}")


@app.post("/positions/{position_id}/unlink")
def unlink(position_id: int, db: Session = Depends(get_db)):
    pos = _position(db, position_id)
    if pos.product:
        pos.product.position_id = None
        db.commit()
    return _back(f"Unlinked {pos.label}")


@app.post("/positions/{position_id}/tare")
def tare(request: Request, position_id: int, db: Session = Depends(get_db)):
    pos = _position(db, position_id)
    return _command(request, pos, {"pos": pos.channel, "action": "tare"}, "Tare sent: plate must be empty")


@app.post("/positions/{position_id}/calibrate")
def calibrate(request: Request, position_id: int, items: int = Form(10), db: Session = Depends(get_db)):
    pos = _position(db, position_id)
    if not 1 <= items <= 20:
        raise HTTPException(400, "items must be between 1 and 20")
    return _command(
        request, pos, {"pos": pos.channel, "action": "calibrate", "items": items},
        f"Calibration sent: {items} items must be on the plate",
    )


def _command(request: Request, pos: Position, payload: dict, ok_msg: str):
    mqtt = request.app.state.mqtt
    if mqtt is None or not mqtt.send_command(pos.device_id, payload):
        return _back("Could not reach the MQTT broker")
    return _back(ok_msg)


@app.post("/positions/{position_id}/sync")
def sync_now(request: Request, position_id: int, db: Session = Depends(get_db)):
    pos = _position(db, position_id)
    result = sync_position(db, request.app.state.shop, pos.id)
    return _back(f"Sync {pos.label}: {result}")


# ---------- JSON API ----------
@app.get("/api/positions")
def api_positions(db: Session = Depends(get_db)):
    return [
        {
            "id": p.id,
            "device": p.device_id,
            "channel": p.channel,
            "count": p.count,
            "status": p.status,
            "calibrated": p.calibrated,
            "product": p.product.title if p.product else None,
            "threshold": p.product.threshold if p.product else None,
            "online_count": p.product.last_synced_count if p.product else None,
        }
        for p in db.scalars(select(Position).order_by(Position.id))
    ]


@app.get("/health")
def health():
    return {"status": "ok"}
