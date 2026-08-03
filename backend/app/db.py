"""Engine + session handling.

Schema creation runs at import-time of :func:`init_db`, which ``main.py`` calls
on startup. There is deliberately no separate migration step to run by hand —
``docker compose up`` has to be enough.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .config import database_url
from .models import Base

_url = database_url()
engine = create_engine(
    _url,
    connect_args={"check_same_thread": False, "timeout": 30} if _url.startswith("sqlite") else {},
    pool_pre_ping=True,
)

if _url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver glue
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _apply_lightweight_migrations()


def _apply_lightweight_migrations() -> None:
    """Add columns that were introduced after a database was first created.

    ``create_all`` never alters existing tables, so a plain additive column
    would silently be missing on an upgraded install. Adding them here keeps
    the "no manual migration step" promise without pulling in Alembic for a
    single-file SQLite database.
    """
    inspector = inspect(engine)
    if "collections" not in inspector.get_table_names():
        return
    expected = {
        "collections": {
            "last_measured_hit_rate": "FLOAT",
            "last_measured_at": "DATETIME",
            "status_detail": "TEXT DEFAULT ''",
            "next_sequence_index": "INTEGER DEFAULT 0",
        },
        "collection_answers": {"ttft_ms": "INTEGER DEFAULT 0"},
        "synthesis_traces": {"ttft_ms": "INTEGER DEFAULT 0"},
    }
    with engine.begin() as conn:
        for table, columns in expected.items():
            if table not in inspector.get_table_names():
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for column, ddl in columns.items():
                if column not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
