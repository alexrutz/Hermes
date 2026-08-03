"""Conversion and cache warm-up.

Ingest is strictly separate from query. Nothing in :mod:`app.query` ever calls
into this module, and nothing here ever answers a user question.

Warm-up sends the collection's complete current prefix with ``max_tokens: 1``,
``temperature: 0`` and thinking disabled. The response is discarded — the point
is purely to make the server materialise L1/L2/L3 entries for that prefix.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from . import prefix as prefix_mod
from . import prompts, settings_store, storage
from .jobs import registry
from .models import Collection, CollectionStatus, Document, Page
from .rendering import RenderOptions, page_count, render_document, write_page_files
from .sglang_client import client_from_settings
from .tokens import VisionConfig

logger = logging.getLogger(__name__)


class BudgetExceeded(RuntimeError):
    def __init__(self, message: str, detail: dict):
        super().__init__(message)
        self.detail = detail


# --------------------------------------------------------------------------- helpers
def vision_config(values: dict) -> VisionConfig:
    return VisionConfig(
        patch_size=int(values["vision_patch_size"]),
        merge_size=int(values["vision_merge_size"]),
        min_pixels=int(values["vision_min_pixels"]),
        max_pixels=int(values["vision_max_pixels"]),
    )


def render_options(
    values: dict, collection: Collection, dpi_override: int | None = None
) -> RenderOptions:
    """Resolve DPI and friends: upload override → collection → global default."""
    return RenderOptions(
        dpi=int(dpi_override or collection.dpi or values["default_pdf_dpi"]),
        max_edge=int(collection.max_edge if collection.max_edge is not None else values["default_max_edge"]),
        image_format=str(collection.image_format or values["default_image_format"]),
        jpeg_quality=int(
            collection.jpeg_quality
            if collection.jpeg_quality is not None
            else values["default_jpeg_quality"]
        ),
        grayscale=bool(
            collection.grayscale
            if collection.grayscale is not None
            else values["default_grayscale"]
        ),
    )


def instruction_for(values: dict, collection: Collection) -> str:
    return str(values["collection_instruction"]).format(collection_name=collection.name)


def collection_overhead(values: dict, collection: Collection) -> int:
    from .tokens import estimate_text_tokens

    return (
        estimate_text_tokens(str(values["global_system_prompt"]))
        + estimate_text_tokens(instruction_for(values, collection))
        + 64  # chat template scaffolding + the question itself
    )


def collection_budget(values: dict, collection: Collection) -> int:
    return prefix_mod.collection_budget(
        int(values["max_context_tokens"]),
        int(values["reserved_output_tokens"]),
        collection_overhead(values, collection),
    )


def collection_token_total(db: Session, collection_id: str, values: dict) -> int:
    """Image tokens plus per-page label tokens, in sequence order."""
    from .tokens import estimate_text_tokens

    label_template = str(values["page_label_template"])
    rows = (
        db.query(Page, Document)
        .join(Document, Page.document_id == Document.id)
        .filter(Page.collection_id == collection_id)
        .order_by(Page.sequence_index.asc())
        .all()
    )
    total = 0
    for page, document in rows:
        total += page.estimated_tokens
        total += estimate_text_tokens(
            label_template.format(
                filename=document.filename,
                page_number=page.page_number,
                document_pages=document.page_count,
                sequence_index=page.sequence_index,
            )
        )
    return total


def mark_stale(collection: Collection, reason: str) -> None:
    collection.status = CollectionStatus.stale.value
    collection.status_detail = reason
    collection.prefix_hash = None
    collection.cached_prefix_token_count = 0


def mark_all_stale(db: Session, reason: str) -> int:
    changed = 0
    for collection in db.query(Collection).all():
        if collection.status != CollectionStatus.pending.value:
            mark_stale(collection, reason)
            changed += 1
    db.commit()
    return changed


def refresh_token_count(db: Session, collection: Collection, values: dict) -> None:
    collection.token_count = collection_token_total(db, collection.id, values)
    db.commit()


# ----------------------------------------------------------------- conversion
def convert_upload(
    db: Session,
    collection: Collection,
    upload_path: Path,
    original_filename: str,
    values: dict,
    dpi_override: int | None = None,
) -> Document:
    """Render one uploaded file into pages appended at the end of the branch.

    Appending keeps the existing prefix valid, so a collection that was already
    ``cached`` stays usable: only the new tail needs warming.
    """
    options = render_options(values, collection, dpi_override)
    vision = vision_config(values)
    total_pages = page_count(upload_path)

    budget = collection_budget(values, collection)
    existing_tokens = collection_token_total(db, collection.id, values)

    document = Document(
        collection_id=collection.id,
        filename=original_filename,
        source_path=str(upload_path),
        content_type="application/pdf" if upload_path.suffix.lower() == ".pdf" else "image/*",
        page_count=total_pages,
        sequence_start=collection.next_sequence_index,
        dpi=options.dpi,
    )
    db.add(document)
    db.flush()

    registry.update(
        collection.id,
        phase="rendering",
        total=total_pages,
        current=0,
        message=f"{original_filename}: {total_pages} Seiten bei {options.dpi} DPI",
    )

    added_tokens = 0
    sequence = collection.next_sequence_index
    written: list[str] = []
    for rendered in render_document(upload_path, options, vision):
        page = Page(
            document_id=document.id,
            collection_id=collection.id,
            sequence_index=sequence,
            page_number=rendered.page_number,
            image_path="",
            base64_path="",
            mime_type=rendered.mime_type,
            width=rendered.width,
            height=rendered.height,
            dpi=options.dpi,
            byte_size=rendered.byte_size,
            estimated_tokens=rendered.estimated_tokens,
            image_hash=rendered.image_hash,
        )
        db.add(page)
        db.flush()

        image_path, base64_path = storage.page_paths(collection.id, page.id, options.suffix)
        write_page_files(rendered, image_path, base64_path)
        page.image_path = str(image_path)
        page.base64_path = str(base64_path)
        written += [str(image_path), str(base64_path)]

        added_tokens += rendered.estimated_tokens
        if existing_tokens + added_tokens > budget:
            db.rollback()
            storage.remove_files(written + [str(upload_path)])
            raise BudgetExceeded(
                f"'{original_filename}' passt nicht mehr ins Kontextfenster.",
                {
                    "budget_tokens": budget,
                    "existing_tokens": existing_tokens,
                    "added_tokens": added_tokens,
                    "overflow_tokens": existing_tokens + added_tokens - budget,
                    "pages_rendered": rendered.page_number,
                    "dpi": options.dpi,
                    "hint": _dpi_hint(options.dpi, existing_tokens + added_tokens, budget),
                },
            )

        sequence += 1
        registry.update(
            collection.id,
            current=rendered.page_number,
            message=f"Seite {rendered.page_number}/{total_pages} — "
            f"{rendered.width}×{rendered.height}px, ~{rendered.estimated_tokens} Tokens",
        )

    collection.next_sequence_index = sequence
    document.page_count = sequence - document.sequence_start
    db.commit()

    refresh_token_count(db, collection, values)
    if collection.status == CollectionStatus.cached.value:
        collection.status = CollectionStatus.stale.value
        collection.status_detail = (
            "Neues Dokument angehängt — der bestehende Präfix bleibt gültig, "
            "nur der neue Teil muss noch ingestiert werden."
        )
    elif collection.status != CollectionStatus.error.value:
        collection.status = CollectionStatus.pending.value
    db.commit()
    return document


def _dpi_hint(dpi: int, tokens: int, budget: int) -> str:
    if tokens <= budget or budget <= 0:
        return ""
    # Token count scales with pixel area, i.e. with the square of the DPI.
    suggested = max(72, int(dpi * (budget / tokens) ** 0.5))
    return f"Bei etwa {suggested} DPI würde die Sammlung wieder ins Fenster passen."


# --------------------------------------------------------------------- removal
def remove_document(db: Session, collection: Collection, document: Document, values: dict) -> dict:
    """Delete a document and decide whether the branch survives.

    Removing the *last* document is a pure truncation: every remaining page keeps
    its sequence index, so the shortened prefix is still a prefix of what the
    server cached and stays usable. Removing anything else shifts every later
    page and breaks the branch.
    """
    documents = (
        db.query(Document)
        .filter(Document.collection_id == collection.id)
        .order_by(Document.sequence_start.asc())
        .all()
    )
    is_last = bool(documents) and documents[-1].id == document.id

    pages = db.query(Page).filter(Page.document_id == document.id).all()
    storage.remove_files([p.image_path for p in pages] + [p.base64_path for p in pages])
    storage.remove_files([document.source_path])

    db.delete(document)
    db.commit()

    if is_last:
        collection.next_sequence_index = document.sequence_start
        if collection.status == CollectionStatus.cached.value:
            collection.status_detail = (
                "Letztes Dokument entfernt — der verbleibende Präfix ist eine echte "
                "Verkürzung und bleibt gecacht."
            )
        invalidated = False
    else:
        _resequence(db, collection)
        mark_stale(
            collection,
            "Ein Dokument aus der Mitte wurde entfernt. Alle nachfolgenden Seiten "
            "verschieben sich, der Präfix ist gebrochen — die Sammlung muss komplett "
            "neu ingestiert werden.",
        )
        invalidated = True

    db.commit()
    refresh_token_count(db, collection, values)
    return {
        "invalidated": invalidated,
        "status": collection.status,
        "status_detail": collection.status_detail,
    }


def _resequence(db: Session, collection: Collection) -> None:
    """Renumber pages to a gapless 0..n-1 run, preserving relative order."""
    documents = (
        db.query(Document)
        .filter(Document.collection_id == collection.id)
        .order_by(Document.sequence_start.asc())
        .all()
    )
    # Two passes with an offset so the unique (collection_id, sequence_index)
    # constraint cannot trip while indices temporarily overlap.
    offset = 1_000_000
    for document in documents:
        for page in sorted(document.pages, key=lambda p: p.sequence_index):
            page.sequence_index += offset
    db.flush()

    next_index = 0
    for document in documents:
        document.sequence_start = next_index
        for page in sorted(document.pages, key=lambda p: p.sequence_index):
            page.sequence_index = next_index
            next_index += 1
        document.page_count = next_index - document.sequence_start
    collection.next_sequence_index = next_index
    db.commit()


def reorder_documents(db: Session, collection: Collection, order: list[str], values: dict) -> dict:
    """Apply a new document order. Always invalidates — the branch is rebuilt."""
    documents = {d.id: d for d in collection.documents}
    if set(order) != set(documents):
        raise ValueError("Die Reihenfolge muss exakt alle Dokumente der Sammlung enthalten.")
    for position, document_id in enumerate(order):
        documents[document_id].sequence_start = position * 1_000_000
    db.flush()
    _resequence(db, collection)
    mark_stale(
        collection,
        "Die Dokumentreihenfolge wurde geändert. Der Präfix ist gebrochen — die "
        "Sammlung muss komplett neu ingestiert werden.",
    )
    db.commit()
    refresh_token_count(db, collection, values)
    return {"invalidated": True, "status": collection.status}


# -------------------------------------------------------------------- warm-up
async def warm_collection(db: Session, collection: Collection, values: dict) -> dict:
    """Send the full current prefix once to materialise it in L1/L2/L3."""
    page_refs = prefix_mod.load_page_refs(db, collection.id)
    if not page_refs:
        raise ValueError("Die Sammlung enthält keine Seiten.")

    system_prompt = str(values["global_system_prompt"])
    label_template = str(values["page_label_template"])

    signature = prefix_mod.prefix_signature(
        system_prompt=system_prompt, page_refs=page_refs, label_template=label_template
    )
    estimated = prefix_mod.estimate_prompt_tokens(
        page_refs,
        system_prompt=system_prompt,
        instruction=instruction_for(values, collection),
        label_template=label_template,
    )
    budget = collection_budget(values, collection)
    if estimated > budget:
        raise BudgetExceeded(
            "Die Sammlung überschreitet das Kontextfenster und kann nicht ingestiert werden.",
            {
                "budget_tokens": budget,
                "estimated_tokens": estimated,
                "overflow_tokens": estimated - budget,
                "pages": len(page_refs),
                "hint": _dpi_hint(
                    int(collection.dpi or values["default_pdf_dpi"]), estimated, budget
                ),
            },
        )

    messages = prefix_mod.build_messages(
        system_prompt=system_prompt,
        page_refs=page_refs,
        collection_name=collection.name,
        collection_instruction_template=str(values["collection_instruction"]),
        label_template=label_template,
        question=prompts.INGEST_WARMUP_QUESTION,
    )

    registry.update(
        collection.id,
        phase="warming",
        current=0,
        total=1,
        message=f"Präfix wird an SGLang gesendet: {len(page_refs)} Seiten, ~{estimated:,} Tokens. "
        "Das erste Prefill kann mehrere Minuten dauern.",
    )

    client = client_from_settings(values)
    before = await client.metrics_safe()

    # max_tokens=1, temperature=0, thinking off: we only want the KV cache, and
    # the answer is thrown away.
    result = await client.chat(
        messages=messages,
        model=str(values["sglang_model"]),
        max_tokens=1,
        temperature=0.0,
        top_p=1.0,
        top_k=-1,
        repetition_penalty=1.0,
        stop=[],
        thinking=False,
    )

    after = await client.metrics_safe()

    collection.prefix_hash = signature
    collection.cached_prefix_token_count = result.prompt_tokens or estimated
    collection.token_count = collection_token_total(db, collection.id, values)
    collection.status = CollectionStatus.cached.value
    collection.status_detail = ""
    db.commit()

    from .metrics import delta_hit_rate

    return {
        "prefix_hash": signature,
        "estimated_tokens": estimated,
        "prompt_tokens": result.prompt_tokens,
        "cached_tokens": result.cached_tokens,
        "cache_hit_rate": result.cache_hit_rate,
        "server_hit_rate": delta_hit_rate(before, after) if before and after else None,
        "latency_ms": result.latency_ms,
        "pages": len(page_refs),
    }


async def run_ingest(collection_id: str) -> None:
    """Background entry point: warm one collection, recording progress."""
    from .db import SessionLocal

    db = SessionLocal()
    try:
        collection = db.get(Collection, collection_id)
        if collection is None:
            registry.finish(collection_id, phase="error", error="Sammlung nicht gefunden.")
            return
        values = settings_store.all_settings(db)
        collection.status = CollectionStatus.ingesting.value
        collection.status_detail = ""
        db.commit()
        try:
            result = await warm_collection(db, collection, values)
        except BudgetExceeded as exc:
            collection.status = CollectionStatus.error.value
            collection.status_detail = f"{exc} {exc.detail.get('hint', '')}".strip()
            db.commit()
            registry.finish(collection_id, phase="error", error=collection.status_detail)
            return
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI verbatim
            logger.exception("Ingest fehlgeschlagen für %s", collection_id)
            collection.status = CollectionStatus.error.value
            collection.status_detail = str(exc)
            db.commit()
            registry.finish(collection_id, phase="error", error=str(exc))
            return
        registry.finish(
            collection_id,
            phase="cached",
            current=1,
            total=1,
            message=f"Im Cache: {result['prompt_tokens']:,} Prompt-Tokens "
            f"({result['pages']} Seiten) in {result['latency_ms'] / 1000:.1f}s",
            result=result,
        )
    finally:
        db.close()
