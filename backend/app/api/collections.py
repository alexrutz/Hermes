from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from .. import cache_tree, ingest, settings_store, storage
from ..db import get_db
from ..jobs import JobConflict, registry
from ..models import Collection, CollectionStatus, Document, Page
from ..rendering import RenderOptions, probe_page_geometry
from ..schemas import CollectionCreate, CollectionUpdate, DpiPreviewRequest, ReorderRequest
from ..tokens import estimate_image_tokens

router = APIRouter(prefix="/api/collections", tags=["collections"])


def _serialize(db: Session, collection: Collection, values: dict) -> dict:
    stats = cache_tree.collection_stats(db, collection, values)
    return {
        "id": collection.id,
        "name": collection.name,
        "description": collection.description,
        "dpi": collection.dpi,
        "effective_dpi": collection.dpi or values["default_pdf_dpi"],
        "image_format": collection.image_format,
        "effective_image_format": collection.image_format or values["default_image_format"],
        "jpeg_quality": collection.jpeg_quality,
        "max_edge": collection.max_edge,
        "grayscale": collection.grayscale,
        "status": collection.status,
        "status_detail": collection.status_detail,
        "prefix_hash": collection.prefix_hash,
        "cached_prefix_token_count": collection.cached_prefix_token_count,
        "last_measured_hit_rate": collection.last_measured_hit_rate,
        "last_measured_at": (
            collection.last_measured_at.isoformat() if collection.last_measured_at else None
        ),
        "created_at": collection.created_at.isoformat(),
        "updated_at": collection.updated_at.isoformat(),
        **stats,
    }


def _get(db: Session, collection_id: str) -> Collection:
    collection = db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(404, "Sammlung nicht gefunden.")
    return collection


@router.get("")
def list_collections(db: Session = Depends(get_db)):
    values = settings_store.all_settings(db)
    rows = db.query(Collection).order_by(Collection.created_at.asc()).all()
    return [_serialize(db, c, values) for c in rows]


@router.post("", status_code=201)
def create_collection(payload: CollectionCreate, db: Session = Depends(get_db)):
    collection = Collection(**payload.model_dump())
    db.add(collection)
    db.commit()
    return _serialize(db, collection, settings_store.all_settings(db))


@router.get("/{collection_id}")
def get_collection(collection_id: str, db: Session = Depends(get_db)):
    collection = _get(db, collection_id)
    values = settings_store.all_settings(db)
    documents = (
        db.query(Document)
        .filter(Document.collection_id == collection_id)
        .order_by(Document.sequence_start.asc())
        .all()
    )
    return {
        **_serialize(db, collection, values),
        "documents": [
            {
                "id": d.id,
                "filename": d.filename,
                "page_count": d.page_count,
                "sequence_start": d.sequence_start,
                "dpi": d.dpi,
                "created_at": d.created_at.isoformat(),
                "is_last": index == len(documents) - 1,
            }
            for index, d in enumerate(documents)
        ],
    }


@router.patch("/{collection_id}")
def update_collection(
    collection_id: str, payload: CollectionUpdate, db: Session = Depends(get_db)
):
    collection = _get(db, collection_id)
    changes = payload.model_dump(exclude_unset=True)

    # Anything that changes the rendered pixels changes the images, and with
    # them the prefix bytes. Same rule as removing a document from the middle.
    render_keys = {"dpi", "image_format", "jpeg_quality", "max_edge", "grayscale"}
    render_changed = any(
        key in changes and changes[key] != getattr(collection, key) for key in render_keys
    )

    for key, value in changes.items():
        setattr(collection, key, value)

    if render_changed and collection.pages:
        ingest.mark_stale(
            collection,
            "Die Konvertierungseinstellungen wurden geändert. Die Bilder müssen neu "
            "gerendert und die Sammlung komplett neu ingestiert werden.",
        )
    db.commit()
    return {
        **_serialize(db, collection, settings_store.all_settings(db)),
        "requires_reconversion": render_changed and bool(collection.pages),
    }


@router.delete("/{collection_id}", status_code=204)
def delete_collection(collection_id: str, db: Session = Depends(get_db)):
    collection = _get(db, collection_id)
    db.delete(collection)
    db.commit()
    storage.remove_collection_files(collection_id)


# ------------------------------------------------------------------- documents
@router.post("/{collection_id}/documents", status_code=201)
async def upload_document(
    collection_id: str,
    file: UploadFile = File(...),
    dpi: int | None = Form(default=None),
    db: Session = Depends(get_db),
):
    collection = _get(db, collection_id)
    values = settings_store.all_settings(db)

    filename = file.filename or "dokument.pdf"
    target = storage.uploads_dir(collection_id) / storage.safe_filename(filename)
    counter = 1
    while target.exists():
        target = target.with_name(f"{target.stem}_{counter}{target.suffix}")
        counter += 1
    target.write_bytes(await file.read())

    try:
        registry.start(collection_id, "convert")
    except JobConflict as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(409, str(exc)) from exc

    try:
        document = await asyncio.to_thread(
            ingest.convert_upload, db, collection, target, filename, values, dpi
        )
    except ingest.BudgetExceeded as exc:
        registry.finish(collection_id, phase="error", error=str(exc))
        raise HTTPException(409, {"message": str(exc), **exc.detail}) from exc
    except Exception as exc:
        registry.finish(collection_id, phase="error", error=str(exc))
        target.unlink(missing_ok=True)
        raise HTTPException(400, f"Konvertierung fehlgeschlagen: {exc}") from exc

    registry.finish(
        collection_id,
        phase="converted",
        message=f"{document.page_count} Seiten konvertiert.",
        result={"document_id": document.id, "pages": document.page_count},
    )
    return {
        "document_id": document.id,
        "pages": document.page_count,
        "collection": _serialize(db, collection, values),
    }


@router.delete("/{collection_id}/documents/{document_id}")
def delete_document(collection_id: str, document_id: str, db: Session = Depends(get_db)):
    collection = _get(db, collection_id)
    document = db.get(Document, document_id)
    if document is None or document.collection_id != collection_id:
        raise HTTPException(404, "Dokument nicht gefunden.")
    values = settings_store.all_settings(db)
    result = ingest.remove_document(db, collection, document, values)
    return {**result, "collection": _serialize(db, collection, values)}


@router.get("/{collection_id}/documents/{document_id}/removal-impact")
def removal_impact(collection_id: str, document_id: str, db: Session = Depends(get_db)):
    """What deleting this document would cost — shown in the confirm dialog."""
    _get(db, collection_id)
    document = db.get(Document, document_id)
    if document is None or document.collection_id != collection_id:
        raise HTTPException(404, "Dokument nicht gefunden.")
    documents = (
        db.query(Document)
        .filter(Document.collection_id == collection_id)
        .order_by(Document.sequence_start.asc())
        .all()
    )
    is_last = documents[-1].id == document_id
    values = settings_store.all_settings(db)
    remaining_pages = (
        db.query(Page)
        .filter(Page.collection_id == collection_id, Page.document_id != document_id)
        .count()
    )
    total_tokens = ingest.collection_token_total(db, collection_id, values)
    doc_tokens = sum(p.estimated_tokens for p in document.pages)
    return {
        "is_last": is_last,
        "invalidates": not is_last,
        "remaining_pages": remaining_pages,
        "remaining_tokens_estimate": max(0, total_tokens - doc_tokens),
        "message": (
            "Das ist das letzte Dokument der Sammlung. Der verbleibende Präfix ist eine "
            "echte Verkürzung und bleibt im Cache gültig."
            if is_last
            else f"Sammlung wird invalidiert und komplett neu ingestiert — "
            f"{remaining_pages} Seiten, geschätzt {max(0, total_tokens - doc_tokens):,} Tokens."
        ),
    }


@router.post("/{collection_id}/reorder")
def reorder(collection_id: str, payload: ReorderRequest, db: Session = Depends(get_db)):
    collection = _get(db, collection_id)
    values = settings_store.all_settings(db)
    try:
        result = ingest.reorder_documents(db, collection, payload.document_ids, values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**result, "collection": _serialize(db, collection, values)}


# ----------------------------------------------------------------------- pages
@router.get("/{collection_id}/pages")
def list_pages(collection_id: str, db: Session = Depends(get_db)):
    _get(db, collection_id)
    rows = (
        db.query(Page, Document)
        .join(Document, Page.document_id == Document.id)
        .filter(Page.collection_id == collection_id)
        .order_by(Page.sequence_index.asc())
        .all()
    )
    return [
        {
            "id": page.id,
            "document_id": page.document_id,
            "filename": document.filename,
            "sequence_index": page.sequence_index,
            "page_number": page.page_number,
            "document_pages": document.page_count,
            "width": page.width,
            "height": page.height,
            "dpi": page.dpi,
            "byte_size": page.byte_size,
            "estimated_tokens": page.estimated_tokens,
            "mime_type": page.mime_type,
            "image_hash": page.image_hash,
            "url": f"/api/collections/{collection_id}/pages/{page.id}/image",
        }
        for page, document in rows
    ]


@router.get("/{collection_id}/pages/{page_id}/image")
def page_image(collection_id: str, page_id: str, db: Session = Depends(get_db)):
    """Serve the exact file that was base64-encoded into the prompt.

    Not a separately generated thumbnail — the preview has to show what the
    model actually sees.
    """
    page = db.get(Page, page_id)
    if page is None or page.collection_id != collection_id:
        raise HTTPException(404, "Seite nicht gefunden.")
    path = Path(page.image_path)
    if not path.exists():
        raise HTTPException(404, "Bilddatei fehlt auf der Platte.")
    return FileResponse(
        path,
        media_type=page.mime_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Image-Width": str(page.width),
            "X-Image-Height": str(page.height),
            "X-Image-Dpi": str(page.dpi),
            "X-Image-Tokens": str(page.estimated_tokens),
        },
    )


# ---------------------------------------------------------------------- ingest
@router.post("/{collection_id}/ingest", status_code=202)
async def start_ingest(collection_id: str, db: Session = Depends(get_db)):
    collection = _get(db, collection_id)
    if not collection.pages:
        raise HTTPException(400, "Die Sammlung enthält keine Seiten.")
    values = settings_store.all_settings(db)
    stats = cache_tree.collection_stats(db, collection, values)
    if stats["over_budget"]:
        raise HTTPException(
            409,
            {
                "message": "Die Sammlung überschreitet das Kontextfenster.",
                **stats,
            },
        )
    try:
        registry.start(collection_id, "ingest", total=1)
    except JobConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    asyncio.create_task(ingest.run_ingest(collection_id))
    return {"status": "started", "collection_id": collection_id}


@router.get("/{collection_id}/ingest/status")
def ingest_status(collection_id: str, db: Session = Depends(get_db)):
    collection = _get(db, collection_id)
    job = registry.get(collection_id)
    return {
        "status": collection.status,
        "status_detail": collection.status_detail,
        "cached_prefix_token_count": collection.cached_prefix_token_count,
        "job": job.snapshot() if job else None,
    }


@router.get("/{collection_id}/progress")
async def progress_stream(collection_id: str):
    """SSE feed of conversion / ingest progress."""

    async def generator():
        while True:
            job = registry.get(collection_id)
            payload = job.snapshot() if job else {"phase": "idle", "finished": True}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if payload.get("finished"):
                break
            await registry.wait(collection_id, timeout=1.0)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------------ dpi helper
@router.post("/{collection_id}/documents/{document_id}/dpi-preview")
def dpi_preview(
    collection_id: str,
    document_id: str,
    payload: DpiPreviewRequest,
    db: Session = Depends(get_db),
):
    document = db.get(Document, document_id)
    if document is None or document.collection_id != collection_id:
        raise HTTPException(404, "Dokument nicht gefunden.")
    values = settings_store.all_settings(db)
    return _estimate_for_file(Path(document.source_path), payload, values)


@router.post("/dpi-preview")
async def dpi_preview_upload(
    file: UploadFile = File(...),
    dpi: int = Form(...),
    max_edge: int = Form(default=0),
    db: Session = Depends(get_db),
):
    """Live estimate for the upload dialog's DPI slider, before committing."""
    values = settings_store.all_settings(db)
    tmp = storage.data_root() / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    target = tmp / storage.safe_filename(file.filename or "upload.pdf")
    target.write_bytes(await file.read())
    try:
        return _estimate_for_file(
            target, DpiPreviewRequest(dpi=dpi, max_edge=max_edge), values
        )
    finally:
        target.unlink(missing_ok=True)


def _estimate_for_file(path: Path, payload: DpiPreviewRequest, values: dict) -> dict:
    vision = ingest.vision_config(values)
    options = RenderOptions(
        dpi=payload.dpi,
        max_edge=payload.max_edge or int(values["default_max_edge"]),
        image_format=str(values["default_image_format"]),
    )
    try:
        sizes = probe_page_geometry(path, options)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Datei konnte nicht gelesen werden: {exc}") from exc

    per_page = [
        {
            "page": index + 1,
            "width": width,
            "height": height,
            "tokens": estimate_image_tokens(width, height, vision),
        }
        for index, (width, height) in enumerate(sizes)
    ]
    total = sum(p["tokens"] for p in per_page)
    return {
        "dpi": payload.dpi,
        "pages": len(per_page),
        "total_tokens": total,
        "avg_tokens_per_page": round(total / len(per_page)) if per_page else 0,
        "context_percent": round(100 * total / int(values["max_context_tokens"]), 1),
        "pixels_per_token_edge": vision.factor,
        "per_page": per_page[:50],
    }
