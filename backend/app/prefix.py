"""The one and only prompt builder.

Every request that this system sends to SGLang — warm-up and query alike — gets
its message array from :func:`build_messages` here. There is no second code
path, because a second code path is exactly how a prefix drifts by one byte and
the entire cache silently stops paying off.

Layout (see ARCHITECTURE.md §1.1)::

    [0] system : GLOBAL_SYSTEM_PROMPT          <- identical for all collections
    [1] user   : label, image, label, image, … <- the cached image block
                 collection instruction        <- after the images
                 question (+ history)          <- the only variable part

:func:`assert_prefix_invariants` is called before every send and raises
:class:`PrefixViolation` rather than letting a broken prefix reach the wire.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from sqlalchemy.orm import Session

from .models import Collection, Document, Page

#: Text that must never appear before the image block. Anything matching these
#: is either a timestamp or a per-request identifier and would break the cache.
_VOLATILE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"),          # ISO timestamps
    re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b"),                      # German dates
    re.compile(r"\b\d{10,13}\b"),                                # epoch seconds/millis
    re.compile(r"\b[0-9a-f]{32}\b", re.IGNORECASE),              # uuid4().hex
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-", re.IGNORECASE),
)


class PrefixViolation(RuntimeError):
    """Raised when a request would not reproduce the cached prefix."""


@dataclass(frozen=True)
class PageRef:
    """Everything the prefix needs about a page — all immutable columns."""

    sequence_index: int
    page_number: int
    document_pages: int
    filename: str
    mime_type: str
    base64_data: str
    estimated_tokens: int


def load_page_refs(db: Session, collection_id: str) -> list[PageRef]:
    """Read a collection's pages in prompt order.

    Ordering is by ``sequence_index`` and by nothing else — not filename, not
    ``created_at``, not the database's natural order.
    """
    rows = (
        db.query(Page, Document)
        .join(Document, Page.document_id == Document.id)
        .filter(Page.collection_id == collection_id)
        .order_by(Page.sequence_index.asc())
        .all()
    )
    refs: list[PageRef] = []
    for page, document in rows:
        refs.append(
            PageRef(
                sequence_index=page.sequence_index,
                page_number=page.page_number,
                document_pages=document.page_count,
                filename=document.filename,
                mime_type=page.mime_type,
                base64_data=read_base64(page.base64_path),
                estimated_tokens=page.estimated_tokens,
            )
        )
    return refs


def read_base64(path: str) -> str:
    """Return the base64 payload exactly as it was written during ingest.

    No re-encoding, no whitespace normalisation, no ``base64.b64encode`` round
    trip. These bytes *are* the cache key.
    """
    with open(path, "r", encoding="ascii") as fh:
        return fh.read()


def render_page_label(template: str, ref: PageRef) -> str:
    return template.format(
        filename=ref.filename,
        page_number=ref.page_number,
        document_pages=ref.document_pages,
        sequence_index=ref.sequence_index,
    )


def build_image_block(page_refs: Sequence[PageRef], label_template: str) -> list[dict[str, Any]]:
    """The cached part: alternating fixed label and image, in sequence order."""
    block: list[dict[str, Any]] = []
    for ref in page_refs:
        block.append({"type": "text", "text": render_page_label(label_template, ref)})
        block.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{ref.mime_type};base64,{ref.base64_data}"},
            }
        )
    return block


def build_messages(
    *,
    system_prompt: str,
    page_refs: Sequence[PageRef],
    collection_name: str,
    collection_instruction_template: str,
    label_template: str,
    question: str,
    history: Sequence[tuple[str, str]] = (),
) -> list[dict[str, Any]]:
    """Assemble the full message array for one collection request.

    ``history`` is appended *after* the image block and after the collection
    instruction. It never precedes the images — that is the whole point.
    """
    image_block = build_image_block(page_refs, label_template)
    instruction = collection_instruction_template.format(collection_name=collection_name)

    tail: list[dict[str, Any]] = [{"type": "text", "text": instruction}]
    if history:
        rendered = "\n\n".join(
            f"{'Nutzer' if role == 'user' else 'Antwort'}: {content}".strip()
            for role, content in history
            if content.strip()
        )
        if rendered:
            tail.append(
                {"type": "text", "text": f"Bisheriger Gesprächsverlauf:\n{rendered}"}
            )
    tail.append({"type": "text", "text": f"Frage: {question}"})

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": image_block + tail},
    ]
    assert_prefix_invariants(messages, system_prompt, len(page_refs))
    return messages


def _content_parts(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    content = messages[1]["content"]
    if not isinstance(content, list):
        raise PrefixViolation("Die User-Nachricht muss eine Liste von Content-Teilen sein.")
    return content


def assert_prefix_invariants(
    messages: Sequence[dict[str, Any]], system_prompt: str, expected_pages: int
) -> None:
    """Refuse to send anything that would not reproduce the cached prefix."""
    if len(messages) != 2:
        raise PrefixViolation(
            f"Erwartet werden genau 2 Nachrichten (System + User), erhalten: {len(messages)}. "
            "Chat-Verlauf darf niemals als eigene Nachricht vor den Bildblock geraten."
        )
    if messages[0].get("role") != "system":
        raise PrefixViolation("Die erste Nachricht muss die System-Nachricht sein.")
    if messages[0].get("content") != system_prompt:
        raise PrefixViolation(
            "Die System-Nachricht weicht vom globalen System-Prompt ab. Sie ist der "
            "gemeinsame Wurzel-Präfix aller Sammlungen und muss überall identisch sein."
        )
    if messages[1].get("role") != "user":
        raise PrefixViolation("Die zweite Nachricht muss die User-Nachricht sein.")

    parts = _content_parts(messages)
    image_positions = [i for i, p in enumerate(parts) if p.get("type") == "image_url"]

    if len(image_positions) != expected_pages:
        raise PrefixViolation(
            f"Erwartet: {expected_pages} Bilder, gefunden: {len(image_positions)}."
        )
    if not image_positions:
        return  # An empty collection has no image block to protect.

    last_image = image_positions[-1]

    # Everything up to and including the last image must be label/image pairs.
    for index in range(0, last_image + 1):
        part = parts[index]
        expected_type = "text" if index % 2 == 0 else "image_url"
        if part.get("type") != expected_type:
            raise PrefixViolation(
                f"Position {index} im Bildblock ist '{part.get('type')}', erwartet "
                f"'{expected_type}'. Der Bildblock muss aus Label/Bild-Paaren bestehen; "
                "nichts Variables darf dazwischen oder davor stehen."
            )
        if expected_type == "text":
            _assert_stable_text(part.get("text", ""), index)

    if last_image != 2 * expected_pages - 1:
        raise PrefixViolation(
            "Zwischen den Bildern liegt zusätzlicher Inhalt. Der Bildblock muss "
            "zusammenhängend am Anfang der User-Nachricht stehen."
        )
    if last_image == len(parts) - 1:
        raise PrefixViolation(
            "Nach dem Bildblock fehlt die Sammlungs-Instruktion. Sie muss NACH den "
            "Bildern stehen, nicht davor."
        )


def _assert_stable_text(text: str, index: int) -> None:
    for pattern in _VOLATILE_PATTERNS:
        match = pattern.search(text)
        if match:
            raise PrefixViolation(
                f"Der Text an Position {index} des Bildblocks enthält den variablen Wert "
                f"'{match.group(0)}'. Zeitstempel, IDs und Ähnliches zerstören den "
                "Cache-Treffer und dürfen nicht vor den Bildern stehen."
            )


def prefix_signature(
    *,
    system_prompt: str,
    page_refs: Sequence[PageRef],
    label_template: str,
) -> str:
    """SHA-256 over exactly the bytes that make up the cacheable prefix.

    Deliberately excludes the collection instruction and the question: those sit
    behind the image block, so changing them costs a short re-prefill but does
    not invalidate the expensive part.
    """
    hasher = hashlib.sha256()
    hasher.update(b"hermes-prefix-v1\x00")
    hasher.update(system_prompt.encode("utf-8"))
    hasher.update(b"\x00")
    for ref in page_refs:
        hasher.update(render_page_label(label_template, ref).encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(ref.mime_type.encode("ascii"))
        hasher.update(b"\x00")
        hasher.update(ref.base64_data.encode("ascii"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def canonical_prefix_json(messages: Sequence[dict[str, Any]]) -> str:
    """Stable serialisation, used by the determinism tests."""
    return json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def estimate_prompt_tokens(
    page_refs: Iterable[PageRef],
    *,
    system_prompt: str,
    instruction: str,
    label_template: str,
) -> int:
    """Estimated prompt tokens for a collection's prefix, excluding the question."""
    from .tokens import estimate_text_tokens

    total = estimate_text_tokens(system_prompt) + estimate_text_tokens(instruction)
    # Chat template scaffolding: role markers, <|im_start|>/<|im_end|> pairs.
    total += 24
    for ref in page_refs:
        total += ref.estimated_tokens
        total += estimate_text_tokens(render_page_label(label_template, ref))
    return total


def collection_budget(max_context: int, reserved_output: int, overhead: int) -> int:
    return max(0, max_context - reserved_output - overhead)


def check_prefix_matches(collection: Collection, signature: str) -> bool:
    """True when the collection's warmed prefix is still byte-identical."""
    return bool(collection.prefix_hash) and collection.prefix_hash == signature
