import os
import sys
from pathlib import Path

# Tests use an in-memory-like SQLite file and never touch MQTT or Shopify.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("MQTT_ENABLED", "false")
os.environ.setdefault("SHOPIFY_DRY_RUN", "true")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app import models  # noqa: E402,F401


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        yield session
