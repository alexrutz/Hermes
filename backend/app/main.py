from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import settings_store
from .api import chats, collections, query, system
from .config import env_config
from .db import SessionLocal, init_db
from .jobs import registry

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)
logger = logging.getLogger("hermes")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Schema creation and setting seeding happen here so that `docker compose up`
    # is genuinely enough — there is no separate migration command to remember.
    init_db()
    db = SessionLocal()
    try:
        settings_store.seed_defaults(db)
    finally:
        db.close()
    logger.info("Hermes bereit. Datenverzeichnis: %s", env_config().data_dir)
    yield


app = FastAPI(
    title="Hermes",
    description="Visuelles CAG-System auf Basis von SGLang HiCache",
    version="1.0.0",
    lifespan=lifespan,
)

origins = env_config().cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if origins.strip() == "*" else [o.strip() for o in origins.split(",")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Image-Width", "X-Image-Height", "X-Image-Dpi", "X-Image-Tokens"],
)

app.include_router(collections.router)
app.include_router(chats.router)
app.include_router(query.router)
app.include_router(system.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "jobs": registry.active()}
