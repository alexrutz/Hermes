from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from .. import cache_tree, ingest, settings_store
from ..db import get_db
from ..models import Collection
from ..schemas import SettingsReset, SettingsUpdate
from ..sglang_client import SGLangError, client_from_settings

router = APIRouter(prefix="/api", tags=["system"])


# ---------------------------------------------------------------------- settings
@router.get("/settings")
def read_settings(db: Session = Depends(get_db)):
    values = settings_store.all_settings(db)
    return {
        "values": values,
        "schema": settings_store.describe(),
        "launch_command": settings_store.build_launch_command(values),
    }


@router.put("/settings")
def write_settings(payload: SettingsUpdate, db: Session = Depends(get_db)):
    try:
        invalidating = settings_store.update(db, payload.values)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc

    affected = 0
    if invalidating:
        affected = ingest.mark_all_stale(
            db,
            "Ein cache-relevanter Prompt wurde geändert ("
            + ", ".join(invalidating)
            + "). Der Präfix aller Sammlungen hat sich verschoben — alles muss neu "
            "ingestiert werden.",
        )

    values = settings_store.all_settings(db)
    return {
        "values": values,
        "launch_command": settings_store.build_launch_command(values),
        "invalidated_keys": invalidating,
        "invalidated_collections": affected,
    }


@router.post("/settings/reset")
def reset_settings(payload: SettingsReset, db: Session = Depends(get_db)):
    invalidating = settings_store.reset(db, payload.keys)
    affected = (
        ingest.mark_all_stale(db, "Prompt auf den Standard zurückgesetzt.")
        if invalidating
        else 0
    )
    values = settings_store.all_settings(db)
    return {
        "values": values,
        "launch_command": settings_store.build_launch_command(values),
        "invalidated_keys": invalidating,
        "invalidated_collections": affected,
    }


# ------------------------------------------------------------------- sglang ops
@router.get("/sglang/health")
async def sglang_health(db: Session = Depends(get_db)):
    client = client_from_settings(settings_store.all_settings(db))
    return await client.health()


@router.get("/sglang/server-info")
async def sglang_server_info(db: Session = Depends(get_db)):
    """Server info plus a diff against the settings we think are in effect."""
    values = settings_store.all_settings(db)
    client = client_from_settings(values)
    try:
        info = await client.server_info()
    except SGLangError as exc:
        raise HTTPException(502, str(exc)) from exc

    flat = _flatten(info)
    mismatches = []
    for setting_key, server_key in settings_store.SERVER_INFO_COMPARISON.items():
        if server_key not in flat:
            continue
        expected = values.get(setting_key)
        actual = flat[server_key]
        if not _equivalent(expected, actual):
            mismatches.append(
                {
                    "key": setting_key,
                    "server_key": server_key,
                    "expected": expected,
                    "actual": actual,
                    "label": settings_store.SPECS[setting_key].label,
                }
            )
    return {"server_info": info, "mismatches": mismatches}


def _flatten(data: dict, prefix: str = "") -> dict:
    out: dict = {}
    for key, value in (data or {}).items():
        if isinstance(value, dict):
            out.update(_flatten(value, prefix))
            out[key] = value
        else:
            out[key] = value
    return out


def _equivalent(expected, actual) -> bool:
    if expected is None or actual is None:
        return expected == actual
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(expected) == bool(actual)
    try:
        return abs(float(expected) - float(actual)) < 1e-6
    except (TypeError, ValueError):
        return str(expected).strip() == str(actual).strip()


@router.get("/sglang/metrics")
async def sglang_metrics(db: Session = Depends(get_db)):
    client = client_from_settings(settings_store.all_settings(db))
    try:
        return await client.metrics()
    except SGLangError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/sglang/metrics/raw", response_class=PlainTextResponse)
async def sglang_metrics_raw(db: Session = Depends(get_db)):
    client = client_from_settings(settings_store.all_settings(db))
    try:
        return await client.metrics_text()
    except SGLangError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/sglang/flush-cache")
async def flush_cache(timeout: float = Query(default=30.0, ge=0), db: Session = Depends(get_db)):
    """Wipe the server-side radix cache. Every collection becomes stale."""
    values = settings_store.all_settings(db)
    client = client_from_settings(values)
    result = await client.flush_cache(timeout)
    if result["ok"]:
        affected = ingest.mark_all_stale(
            db, "Der Server-Cache wurde geleert. Alle Sammlungen müssen neu ingestiert werden."
        )
        result["invalidated_collections"] = affected
    return result


# ------------------------------------------------------------------- cache tree
@router.get("/cache/tree")
def cache_tree_endpoint(db: Session = Depends(get_db)):
    return cache_tree.build_tree(db)


@router.get("/cache/dashboard")
async def cache_dashboard(db: Session = Depends(get_db)):
    values = settings_store.all_settings(db)
    client = client_from_settings(values)
    metrics = await client.metrics_safe()
    tree = cache_tree.build_tree(db)
    collections = db.query(Collection).all()
    return {
        "tree": tree,
        "metrics": metrics,
        "metrics_available": metrics is not None,
        "measured": {
            c.id: {
                "hit_rate": c.last_measured_hit_rate,
                "at": c.last_measured_at.isoformat() if c.last_measured_at else None,
            }
            for c in collections
        },
    }
