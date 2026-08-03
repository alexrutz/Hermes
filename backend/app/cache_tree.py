"""Reconstruct the HiRadixTree branch structure from our own ingest metadata.

SGLang exposes no radix-tree dump (verified against the full route list of
``entrypoints/http_server.py``: there is ``/get_server_info``, ``/flush_cache``,
``/hicache/storage-backend`` and an optional debug ``/dumper/{method}`` — none
of them returns the tree). So the tree here is *derived*, not observed, and
every node says which of its fields are measured and which are inferred. The UI
renders that distinction rather than pretending to a precision we do not have.

What is genuinely measured:
  * ``measured_hit_rate`` per collection — ``cached_tokens / prompt_tokens``
    reported by the server for the last query against that branch.
  * the ``/metrics`` gauges in the dashboard payload.

What is inferred:
  * the tree shape (it mirrors the prefix we send, so it is what the server
    must build, but we never see the server's actual nodes)
  * ``cache_level`` — a heuristic label, see :func:`infer_cache_level`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from . import settings_store
from .ingest import collection_token_total
from .models import Collection, CollectionStatus, Document, Page
from .tokens import estimate_text_tokens

#: A branch whose last measurement is older than this is reported as unknown
#: rather than as cached — L1 eviction is invisible to us.
STALE_MEASUREMENT_SECONDS = 3600


def infer_cache_level(collection: Collection, now: datetime) -> tuple[str, str]:
    """Return ``(level, basis)`` where basis is 'gemessen' or 'abgeleitet'."""
    if collection.status != CollectionStatus.cached.value:
        return "none", "gemessen"

    rate = collection.last_measured_hit_rate
    measured_at = collection.last_measured_at
    if rate is None or measured_at is None:
        # Warmed with write_through, so it should be in L2/L3 — but nothing has
        # confirmed it since.
        return "L3", "abgeleitet"

    if measured_at.tzinfo is None:
        measured_at = measured_at.replace(tzinfo=timezone.utc)
    age = (now - measured_at).total_seconds()

    if rate < 0.5:
        return "none", "gemessen"
    if age <= 120:
        return "L1", "abgeleitet"
    if age <= STALE_MEASUREMENT_SECONDS:
        return "L2", "abgeleitet"
    return "L3", "abgeleitet"


def build_tree(db: Session) -> dict[str, Any]:
    values = settings_store.all_settings(db)
    system_prompt = str(values["global_system_prompt"])
    label_template = str(values["page_label_template"])
    root_tokens = estimate_text_tokens(system_prompt)
    now = datetime.now(timezone.utc)

    collections = db.query(Collection).order_by(Collection.created_at.asc()).all()
    branches: list[dict[str, Any]] = []

    for collection in collections:
        documents = (
            db.query(Document)
            .filter(Document.collection_id == collection.id)
            .order_by(Document.sequence_start.asc())
            .all()
        )
        level, basis = infer_cache_level(collection, now)
        offset = root_tokens
        document_nodes: list[dict[str, Any]] = []

        for document in documents:
            pages = (
                db.query(Page)
                .filter(Page.document_id == document.id)
                .order_by(Page.sequence_index.asc())
                .all()
            )
            document_start = offset
            page_nodes: list[dict[str, Any]] = []
            for page in pages:
                label_tokens = estimate_text_tokens(
                    label_template.format(
                        filename=document.filename,
                        page_number=page.page_number,
                        document_pages=document.page_count,
                        sequence_index=page.sequence_index,
                    )
                )
                node_tokens = page.estimated_tokens + label_tokens
                page_nodes.append(
                    {
                        "type": "page",
                        "id": page.id,
                        "label": f"S. {page.page_number}",
                        "page_number": page.page_number,
                        "sequence_index": page.sequence_index,
                        "tokens": node_tokens,
                        "token_offset": offset,
                        "width": page.width,
                        "height": page.height,
                        "dpi": page.dpi,
                        "byte_size": page.byte_size,
                        "image_hash": page.image_hash[:12],
                        "cache_level": level,
                        "cache_level_basis": basis,
                    }
                )
                offset += node_tokens

            document_nodes.append(
                {
                    "type": "document",
                    "id": document.id,
                    "label": document.filename,
                    "pages": len(page_nodes),
                    "tokens": offset - document_start,
                    "token_offset": document_start,
                    "dpi": document.dpi,
                    "sequence_start": document.sequence_start,
                    "cache_level": level,
                    "cache_level_basis": basis,
                    "children": page_nodes,
                }
            )

        branches.append(
            {
                "type": "collection",
                "id": collection.id,
                "label": collection.name,
                "status": collection.status,
                "status_detail": collection.status_detail,
                "tokens": offset - root_tokens,
                "token_offset": root_tokens,
                "cached_prefix_token_count": collection.cached_prefix_token_count,
                "prefix_hash": (collection.prefix_hash or "")[:12],
                "measured_hit_rate": collection.last_measured_hit_rate,
                "measured_at": (
                    collection.last_measured_at.isoformat()
                    if collection.last_measured_at
                    else None
                ),
                "cache_level": level,
                "cache_level_basis": basis,
                "children": document_nodes,
            }
        )

    return {
        "root": {
            "type": "root",
            "id": "root",
            "label": "Globaler System-Prompt",
            "tokens": root_tokens,
            "token_offset": 0,
            "cache_level": "L1" if branches else "none",
            "cache_level_basis": "abgeleitet",
            "children": branches,
        },
        "totals": {
            "collections": len(collections),
            "documents": sum(len(b["children"]) for b in branches),
            "pages": sum(len(d["children"]) for b in branches for d in b["children"]),
            "tokens": root_tokens + sum(b["tokens"] for b in branches),
            "cached_collections": sum(
                1 for c in collections if c.status == CollectionStatus.cached.value
            ),
        },
        "provenance": {
            "structure": "abgeleitet",
            "structure_note": (
                "SGLang bietet keinen Endpunkt zum Auslesen des Radix-Trees. Diese "
                "Struktur ist aus den eigenen Ingest-Metadaten rekonstruiert und "
                "entspricht der Präfix-Struktur, die im Server entstehen muss."
            ),
            "cache_level": "abgeleitet",
            "cache_level_note": (
                "Aus Status, gemessener Trefferquote und deren Alter geschätzt. "
                "Verdrängung aus L1 ist von außen nicht beobachtbar."
            ),
            "hit_rate": "gemessen",
            "hit_rate_note": (
                "cached_tokens / prompt_tokens aus der letzten Antwort des Servers "
                "für diesen Ast."
            ),
        },
    }


def collection_stats(db: Session, collection: Collection, values: dict) -> dict[str, Any]:
    from .ingest import collection_budget

    tokens = collection_token_total(db, collection.id, values)
    budget = collection_budget(values, collection)
    pages = db.query(Page).filter(Page.collection_id == collection.id).count()
    return {
        "pages": pages,
        "documents": len(collection.documents),
        "estimated_tokens": tokens,
        "budget_tokens": budget,
        "context_tokens": int(values["max_context_tokens"]),
        "budget_used_percent": round(100 * tokens / budget, 1) if budget else 0.0,
        "context_used_percent": round(100 * tokens / int(values["max_context_tokens"]), 1),
        "over_budget": tokens > budget,
    }
