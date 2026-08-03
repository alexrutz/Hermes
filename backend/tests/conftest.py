from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="hermes-tests-")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/test.db")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def data_dir() -> Path:
    return Path(_TMP)


@pytest.fixture()
def db():
    from app.db import SessionLocal, init_db
    from app.models import Base
    from app.db import engine
    from app import settings_store

    Base.metadata.drop_all(engine)
    init_db()
    session = SessionLocal()
    settings_store.seed_defaults(session)
    try:
        yield session
    finally:
        session.close()
