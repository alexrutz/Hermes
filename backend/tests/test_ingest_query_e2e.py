"""End-to-end over a mocked SGLang: convert → ingest → query → remove → re-ingest.

Covers acceptance criteria 2, 4, 5 and 9 without needing a GPU.
"""

from __future__ import annotations

import asyncio
import json

import fitz
import pytest
import respx
from httpx import Response

from app import ingest, prefix as prefix_mod, query as query_mod, settings_store
from app.models import Chat, Collection, CollectionStatus, Document

BASE = "http://sglang.test"


def make_pdf(path, pages: int, text: str = "Miete 1200 EUR") -> None:
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"{text} — Seite {index + 1}", fontsize=18)
    doc.save(str(path))
    doc.close()


@pytest.fixture()
def wired(db, data_dir):
    settings_store.update(
        db,
        {
            "sglang_base_url": BASE,
            "sglang_web_password": "geheim",
            "sglang_max_retries": 0,
            "default_pdf_dpi": 100,
            "default_max_edge": 900,
        },
    )
    return settings_store.all_settings(db)


def add_collection(db, name="Verträge") -> Collection:
    collection = Collection(name=name)
    db.add(collection)
    db.commit()
    return collection


def add_document(db, collection, data_dir, filename, pages, values) -> Document:
    source = data_dir / filename
    make_pdf(source, pages)
    return ingest.convert_upload(db, collection, source, filename, values)


def chat_completion(content="Miete: 1200 € (vertrag.pdf, S. 1)", prompt=1000, cached=0):
    return {
        "choices": [
            {"message": {"content": content, "reasoning_content": "kurz nachgedacht"}}
        ],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": 12,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    }


def sse_stream(content: str, reasoning: str = "denke nach", prompt=1000, cached=950) -> bytes:
    frames = [
        {"choices": [{"delta": {"reasoning_content": reasoning}}]},
        {"choices": [{"delta": {"content": content}}]},
        {
            "choices": [{"delta": {}}],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": cached},
            },
        },
    ]
    body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
    return body.encode()


METRICS = """# HELP sglang:cache_hit_rate x
sglang:cache_hit_rate 0.42
sglang:prompt_tokens_total 1000
sglang:cached_tokens_total 500
sglang:hicache_host_used_tokens 12000
sglang:hicache_host_total_tokens 100000
"""


# --------------------------------------------------------------------- ingest
def test_conversion_appends_pages_in_sequence_order(db, data_dir, wired):
    collection = add_collection(db)
    add_document(db, collection, data_dir, "vertrag.pdf", 3, wired)
    add_document(db, collection, data_dir, "anhang.pdf", 2, wired)

    refs = prefix_mod.load_page_refs(db, collection.id)
    assert [r.sequence_index for r in refs] == [0, 1, 2, 3, 4]
    assert [r.filename for r in refs] == ["vertrag.pdf"] * 3 + ["anhang.pdf"] * 2
    assert collection.next_sequence_index == 5


def test_base64_is_written_once_and_read_back_verbatim(db, data_dir, wired):
    collection = add_collection(db)
    add_document(db, collection, data_dir, "vertrag.pdf", 2, wired)
    first = prefix_mod.load_page_refs(db, collection.id)
    second = prefix_mod.load_page_refs(db, collection.id)
    assert [r.base64_data for r in first] == [r.base64_data for r in second]
    assert all(r.base64_data for r in first)


@respx.mock
def test_ingest_warms_the_prefix_and_records_the_hash(db, data_dir, wired):
    respx.get(f"{BASE}/metrics").mock(return_value=Response(200, text=METRICS))
    route = respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=Response(200, json=chat_completion(prompt=5000))
    )

    collection = add_collection(db)
    add_document(db, collection, data_dir, "vertrag.pdf", 2, wired)

    result = asyncio.run(ingest.warm_collection(db, collection, wired))

    assert collection.status == CollectionStatus.cached.value
    assert collection.prefix_hash == result["prefix_hash"]
    assert collection.cached_prefix_token_count == 5000

    sent = json.loads(route.calls[0].request.content)
    assert sent["max_tokens"] == 1, "Warm-up darf nur ein Token generieren"
    assert sent["temperature"] == 0.0
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}
    assert route.calls[0].request.headers["Authorization"] == "Bearer geheim"


@respx.mock
def test_ingest_and_query_share_the_image_prefix_byte_for_byte(db, data_dir, wired):
    """The point of the whole system: warm-up and query must hit the same node."""
    respx.get(f"{BASE}/metrics").mock(return_value=Response(200, text=METRICS))
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=Response(200, json=chat_completion())
    )

    collection = add_collection(db)
    add_document(db, collection, data_dir, "vertrag.pdf", 2, wired)
    asyncio.run(ingest.warm_collection(db, collection, wired))

    warm_call = next(
        c for c in respx.calls if "chat/completions" in str(c.request.url)
    )
    warm_request = json.loads(warm_call.request.content)

    refs = prefix_mod.load_page_refs(db, collection.id)
    query_messages = prefix_mod.build_messages(
        system_prompt=wired["global_system_prompt"],
        page_refs=refs,
        collection_name=collection.name,
        collection_instruction_template=wired["collection_instruction"],
        label_template=wired["page_label_template"],
        question="Wie hoch ist die Miete?",
    )

    warm_parts = warm_request["messages"][1]["content"]
    query_parts = query_messages[1]["content"]
    image_block_length = 2 * len(refs)

    assert warm_request["messages"][0] == query_messages[0]
    assert warm_parts[:image_block_length] == query_parts[:image_block_length]
    # The instruction is shared too; only the trailing question differs.
    assert warm_parts[image_block_length] == query_parts[image_block_length]
    assert warm_parts[-1] != query_parts[-1]


@respx.mock
def test_ingest_is_blocked_when_the_collection_exceeds_the_window(db, data_dir, wired):
    settings_store.update(db, {"max_context_tokens": 4000, "reserved_output_tokens": 1000})
    values = settings_store.all_settings(db)
    collection = add_collection(db)
    with pytest.raises(ingest.BudgetExceeded) as exc:
        add_document(db, collection, data_dir, "riesig.pdf", 6, values)
    assert exc.value.detail["overflow_tokens"] > 0
    assert "DPI" in exc.value.detail["hint"]


# -------------------------------------------------------------- removal rules
def test_removing_the_last_document_keeps_the_branch_valid(db, data_dir, wired):
    collection = add_collection(db)
    add_document(db, collection, data_dir, "a.pdf", 2, wired)
    last = add_document(db, collection, data_dir, "b.pdf", 2, wired)
    collection.status = CollectionStatus.cached.value
    collection.prefix_hash = "deadbeef"
    db.commit()

    result = ingest.remove_document(db, collection, last, wired)

    assert result["invalidated"] is False
    assert collection.status == CollectionStatus.cached.value
    assert collection.prefix_hash == "deadbeef"
    assert [r.sequence_index for r in prefix_mod.load_page_refs(db, collection.id)] == [0, 1]


def test_removing_a_middle_document_marks_the_collection_stale(db, data_dir, wired):
    """Acceptance criterion 9."""
    collection = add_collection(db)
    add_document(db, collection, data_dir, "a.pdf", 2, wired)
    middle = add_document(db, collection, data_dir, "b.pdf", 2, wired)
    add_document(db, collection, data_dir, "c.pdf", 2, wired)
    collection.status = CollectionStatus.cached.value
    collection.prefix_hash = "deadbeef"
    db.commit()

    result = ingest.remove_document(db, collection, middle, wired)

    assert result["invalidated"] is True
    assert collection.status == CollectionStatus.stale.value
    assert collection.prefix_hash is None
    refs = prefix_mod.load_page_refs(db, collection.id)
    assert [r.sequence_index for r in refs] == [0, 1, 2, 3], "Sequenz muss lückenlos sein"
    assert [r.filename for r in refs] == ["a.pdf", "a.pdf", "c.pdf", "c.pdf"]


@respx.mock
def test_reingest_after_removal_restores_the_cache(db, data_dir, wired):
    """Second half of acceptance criterion 9."""
    respx.get(f"{BASE}/metrics").mock(return_value=Response(200, text=METRICS))
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=Response(200, json=chat_completion(prompt=3000))
    )

    collection = add_collection(db)
    add_document(db, collection, data_dir, "a.pdf", 2, wired)
    middle = add_document(db, collection, data_dir, "b.pdf", 2, wired)
    add_document(db, collection, data_dir, "c.pdf", 2, wired)
    asyncio.run(ingest.warm_collection(db, collection, wired))
    original_hash = collection.prefix_hash

    ingest.remove_document(db, collection, middle, wired)
    assert collection.status == CollectionStatus.stale.value

    asyncio.run(ingest.warm_collection(db, collection, wired))
    assert collection.status == CollectionStatus.cached.value
    assert collection.prefix_hash and collection.prefix_hash != original_hash


def test_reordering_documents_invalidates_the_collection(db, data_dir, wired):
    collection = add_collection(db)
    first = add_document(db, collection, data_dir, "a.pdf", 2, wired)
    second = add_document(db, collection, data_dir, "b.pdf", 1, wired)
    collection.status = CollectionStatus.cached.value
    db.commit()

    ingest.reorder_documents(db, collection, [second.id, first.id], wired)

    assert collection.status == CollectionStatus.stale.value
    refs = prefix_mod.load_page_refs(db, collection.id)
    assert [r.filename for r in refs] == ["b.pdf", "a.pdf", "a.pdf"]
    assert [r.sequence_index for r in refs] == [0, 1, 2]


def test_changing_the_global_system_prompt_invalidates_everything(db, data_dir, wired):
    collection = add_collection(db)
    add_document(db, collection, data_dir, "a.pdf", 1, wired)
    collection.status = CollectionStatus.cached.value
    db.commit()

    invalidating = settings_store.update(db, {"global_system_prompt": "Neuer Prompt"})
    assert invalidating == ["global_system_prompt"]
    ingest.mark_all_stale(db, "test")
    db.refresh(collection)
    assert collection.status == CollectionStatus.stale.value


# ----------------------------------------------------------------------- query
def _collect(db, ctx) -> list[tuple[str, dict]]:
    async def run():
        events = []
        async for frame in query_mod.run_query(db, ctx):
            event = frame.split("\n")[0].removeprefix("event: ")
            data = json.loads(frame.split("data: ", 1)[1].strip())
            events.append((event, data))
        return events

    return asyncio.run(run())


@respx.mock
def test_query_produces_one_answer_per_collection_plus_a_synthesis(db, data_dir, wired):
    """Acceptance criterion 4."""
    respx.get(f"{BASE}/metrics").mock(return_value=Response(200, text=METRICS))
    respx.post(f"{BASE}/v1/chat/completions").mock(
        side_effect=[
            Response(200, content=sse_stream("Miete 1200 € (a.pdf, S. 1)")),
            Response(200, content=sse_stream("Kaution 3600 € (b.pdf, S. 1)")),
            Response(200, content=sse_stream("Die Miete beträgt 1200 €, die Kaution 3600 €.")),
        ]
    )

    a = add_collection(db, "Verträge")
    add_document(db, a, data_dir, "a.pdf", 1, wired)
    b = add_collection(db, "Rechnungen")
    add_document(db, b, data_dir, "b.pdf", 1, wired)

    chat = Chat(title="Neuer Chat")
    db.add(chat)
    db.commit()

    ctx = query_mod.prepare_context(db, chat.id, "Wie hoch ist die Miete?", [a.id, b.id])
    events = _collect(db, ctx)
    kinds = [e for e, _ in events]

    assert kinds.count("collection_start") == 2
    assert kinds.count("collection_done") == 2
    assert "synthesis_start" in kinds
    assert "collection_reasoning" in kinds
    assert "synthesis_reasoning" in kinds

    done = next(data for kind, data in events if kind == "done")
    assert len(done["collections"]) == 2
    assert all(c["cache_hit_rate"] == 0.95 for c in done["collections"])

    stored = db.query(query_mod.CollectionAnswer).all()
    assert len(stored) == 2
    assert all(answer.reasoning_text for answer in stored)
    trace = db.query(query_mod.SynthesisTrace).one()
    assert trace.reasoning_text and not trace.skipped


@respx.mock
def test_query_runs_sequentially_by_default(db, data_dir, wired):
    respx.get(f"{BASE}/metrics").mock(return_value=Response(200, text=METRICS))
    order: list[str] = []

    def handler(request):
        body = json.loads(request.content)
        order.append(body["messages"][1]["content"][-1]["text"][:40])
        return Response(200, content=sse_stream("ok"))

    respx.post(f"{BASE}/v1/chat/completions").mock(side_effect=handler)

    a = add_collection(db, "A")
    add_document(db, a, data_dir, "a.pdf", 1, wired)
    b = add_collection(db, "B")
    add_document(db, b, data_dir, "b.pdf", 1, wired)
    chat = Chat()
    db.add(chat)
    db.commit()

    ctx = query_mod.prepare_context(db, chat.id, "Frage?", [a.id, b.id])
    events = _collect(db, ctx)
    starts = [d["collection_name"] for k, d in events if k == "collection_start"]
    dones = [d["collection_name"] for k, d in events if k == "collection_done"]
    # Sequential: each collection finishes before the next one starts.
    assert starts == ["A", "B"]
    assert dones == ["A", "B"]


@respx.mock
def test_synthesis_is_skipped_for_a_single_collection(db, data_dir, wired):
    respx.get(f"{BASE}/metrics").mock(return_value=Response(200, text=METRICS))
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=Response(200, content=sse_stream("Miete 1200 € (a.pdf, S. 1)"))
    )
    collection = add_collection(db)
    add_document(db, collection, data_dir, "a.pdf", 1, wired)
    chat = Chat()
    db.add(chat)
    db.commit()

    ctx = query_mod.prepare_context(db, chat.id, "Frage?", [collection.id])
    events = _collect(db, ctx)
    assert "synthesis_skipped" in [k for k, _ in events]
    assert len(respx.calls) >= 1
    chat_calls = [c for c in respx.calls if "chat/completions" in str(c.request.url)]
    assert len(chat_calls) == 1, "Ohne Synthese darf es nur einen Generierungs-Request geben"


@respx.mock
def test_query_flags_a_drifted_prefix_instead_of_hiding_the_miss(db, data_dir, wired):
    respx.get(f"{BASE}/metrics").mock(return_value=Response(200, text=METRICS))
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=Response(200, content=sse_stream("ok"))
    )
    collection = add_collection(db)
    add_document(db, collection, data_dir, "a.pdf", 1, wired)
    collection.status = CollectionStatus.cached.value
    collection.prefix_hash = "passt-nicht-mehr"
    db.commit()

    chat = Chat()
    db.add(chat)
    db.commit()
    ctx = query_mod.prepare_context(db, chat.id, "Frage?", [collection.id])
    events = _collect(db, ctx)

    start = next(d for k, d in events if k == "collection_start")
    assert start["prefix_matched"] is False
    db.refresh(collection)
    assert collection.status == CollectionStatus.stale.value


def test_synthesis_prompt_hides_the_mechanics(db, wired):
    """Acceptance criterion 5, at the prompt level."""
    runs = [
        query_mod.CollectionRun("1", "Verträge", answer="Miete 1200 € (a.pdf, S. 1)"),
        query_mod.CollectionRun("2", "Rechnungen", answer="KEIN TREFFER"),
        query_mod.CollectionRun("3", "Sonstiges", answer="Kaution 3600 € (b.pdf, S. 2)"),
    ]
    messages = query_mod.build_synthesis_messages(wired, "Wie hoch ist die Miete?", runs, [])
    body = messages[1]["content"]

    # No collection is named or numbered anywhere in the synthesis input.
    for forbidden in ("Verträge", "Rechnungen", "Sonstiges", "Sammlung 1", "Teilantwort"):
        assert forbidden not in body
    # No-hit results are filtered out entirely rather than fed in as noise.
    assert "KEIN TREFFER" not in body
    assert "Miete 1200 €" in body and "Kaution 3600 €" in body
    assert "Basierend auf den Sammlungen" in messages[0]["content"]  # named as forbidden


def test_no_hit_only_produces_a_clean_negative(db, wired):
    runs = [query_mod.CollectionRun("1", "A", answer="KEIN TREFFER")]
    messages = query_mod.build_synthesis_messages(wired, "Frage?", runs, [])
    assert "nichts Relevantes ergeben" in messages[1]["content"]
