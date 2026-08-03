"""Query orchestration: n collection requests, then one synthesis.

Never ingests. If a collection's prefix no longer matches what was warmed, the
query still runs (the user asked a question and deserves an answer) but the
collection is flagged ``stale`` and the UI says so, rather than quietly serving
a cache miss that looks like a hit.

Default execution is sequential. Firing n large-prefix requests at once makes
them evict each other from the GPU pool, which turns L1 hits into L2/L3 fetches
and costs more than it saves. ``parallel_collection_queries`` opts out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from sqlalchemy.orm import Session

from . import prefix as prefix_mod
from . import settings_store
from .ingest import instruction_for
from .metrics import delta_hit_rate
from .models import (
    Chat,
    Collection,
    CollectionAnswer,
    CollectionStatus,
    Message,
    SynthesisTrace,
)
from .sglang_client import SGLangClient, apply_usage, client_from_settings, sampling_kwargs
from .sglang_client import CompletionResult

logger = logging.getLogger(__name__)


@dataclass
class CollectionRun:
    collection_id: str
    collection_name: str
    answer: str = ""
    reasoning: str = ""
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    ttft_ms: int = 0
    server_hit_rate: float | None = None
    prefix_matched: bool = True
    pages: int = 0
    estimated_prefix_tokens: int = 0
    error: str = ""

    @property
    def cache_hit_rate(self) -> float:
        return self.cached_tokens / self.prompt_tokens if self.prompt_tokens else 0.0

    def header(self) -> dict[str, Any]:
        return {
            "collection_id": self.collection_id,
            "collection_name": self.collection_name,
            "pages": self.pages,
            "estimated_prefix_tokens": self.estimated_prefix_tokens,
            "prefix_matched": self.prefix_matched,
        }

    def stats(self) -> dict[str, Any]:
        return {
            "collection_id": self.collection_id,
            "collection_name": self.collection_name,
            "latency_ms": self.latency_ms,
            "ttft_ms": self.ttft_ms,
            "prompt_tokens": self.prompt_tokens,
            "cached_tokens": self.cached_tokens,
            "completion_tokens": self.completion_tokens,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
            "server_hit_rate": (
                round(self.server_hit_rate, 4) if self.server_hit_rate is not None else None
            ),
            "prefix_matched": self.prefix_matched,
            "error": self.error,
        }


@dataclass
class QueryContext:
    question: str
    chat_id: str
    values: dict[str, Any]
    collections: list[Collection]
    history: list[tuple[str, str]] = field(default_factory=list)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def load_history(db: Session, chat_id: str, turns: int) -> list[tuple[str, str]]:
    """Prior turns, oldest first.

    The router persists the incoming user message before the query runs so it
    survives a failure, so any trailing user messages are the current question
    and are dropped here — they get appended separately, after the image block.
    """
    if turns <= 0:
        return []
    rows = (
        db.query(Message)
        .filter(Message.chat_id == chat_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )
    while rows and rows[-1].role == "user":
        rows.pop()
    return [(m.role, m.content) for m in rows[-turns:]]


def _is_no_hit(text: str, marker: str) -> bool:
    stripped = text.strip().strip(".!").upper()
    return not stripped or stripped == marker.strip().upper()


async def _run_collection(
    db: Session,
    client: SGLangClient,
    collection: Collection,
    ctx: QueryContext,
    emit,
) -> CollectionRun:
    values = ctx.values
    run = CollectionRun(collection_id=collection.id, collection_name=collection.name)

    page_refs = prefix_mod.load_page_refs(db, collection.id)
    run.pages = len(page_refs)
    if not page_refs:
        run.error = "Die Sammlung enthält keine Seiten."
        await emit("collection_error", {"collection_id": collection.id, "error": run.error})
        return run

    system_prompt = str(values["global_system_prompt"])
    label_template = str(values["page_label_template"])

    signature = prefix_mod.prefix_signature(
        system_prompt=system_prompt, page_refs=page_refs, label_template=label_template
    )
    run.prefix_matched = prefix_mod.check_prefix_matches(collection, signature)
    if not run.prefix_matched and collection.status == CollectionStatus.cached.value:
        # The warmed bytes and the bytes we are about to send differ. Say so
        # instead of letting the user read a cache miss as a cache hit.
        collection.status = CollectionStatus.stale.value
        collection.status_detail = (
            "Der Präfix weicht von dem ab, was ingestiert wurde — diese Anfrage läuft "
            "ohne Cache-Treffer. Bitte neu ingestieren."
        )
        db.commit()

    run.estimated_prefix_tokens = prefix_mod.estimate_prompt_tokens(
        page_refs,
        system_prompt=system_prompt,
        instruction=instruction_for(values, collection),
        label_template=label_template,
    )

    messages = prefix_mod.build_messages(
        system_prompt=system_prompt,
        page_refs=page_refs,
        collection_name=collection.name,
        collection_instruction_template=str(values["collection_instruction"]),
        label_template=label_template,
        question=ctx.question,
        history=ctx.history,
    )

    await emit("collection_start", run.header())

    before = await client.metrics_safe()
    try:
        async for chunk in client.chat_stream(
            messages=messages, **sampling_kwargs(values, step="collection")
        ):
            if chunk.kind == "reasoning":
                run.reasoning += chunk.text
                await emit(
                    "collection_reasoning",
                    {"collection_id": collection.id, "text": chunk.text},
                )
            elif chunk.kind == "content":
                run.answer += chunk.text
                await emit(
                    "collection_delta", {"collection_id": collection.id, "text": chunk.text}
                )
            elif chunk.kind == "usage":
                tmp = CompletionResult()
                apply_usage(tmp, chunk.usage)
                run.prompt_tokens = tmp.prompt_tokens or run.prompt_tokens
                run.cached_tokens = tmp.cached_tokens or run.cached_tokens
                run.completion_tokens = tmp.completion_tokens or run.completion_tokens
            elif chunk.kind == "done":
                run.latency_ms = int(chunk.usage.get("latency_ms", 0))
                run.ttft_ms = int(chunk.usage.get("ttft_ms", 0))
    except Exception as exc:  # noqa: BLE001 - one failing branch must not kill the query
        logger.exception("Sammlungsanfrage fehlgeschlagen: %s", collection.name)
        run.error = str(exc)
        await emit("collection_error", {"collection_id": collection.id, "error": run.error})
        return run

    after = await client.metrics_safe()
    if before and after:
        run.server_hit_rate = delta_hit_rate(before, after)

    collection.last_measured_hit_rate = run.cache_hit_rate
    collection.last_measured_at = datetime.now(timezone.utc)
    db.commit()

    await emit("collection_done", run.stats())
    return run


def build_synthesis_messages(
    values: dict[str, Any], question: str, runs: list[CollectionRun], history: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    """The synthesis prompt sees answers only — never images, never collection ids."""
    marker = str(values["no_hit_marker"])
    useful = [r for r in runs if not r.error and not _is_no_hit(r.answer, marker)]

    if useful:
        # Deliberately unlabelled and unnumbered: giving the model "Sammlung 1:"
        # headings is the fastest way to get "Laut Sammlung 1 …" back out.
        findings = "\n\n---\n\n".join(r.answer.strip() for r in useful)
        body = f"Frage des Nutzers:\n{question}\n\nVorliegende Rechercheergebnisse:\n\n{findings}"
    else:
        body = (
            f"Frage des Nutzers:\n{question}\n\n"
            "Die Recherche hat nichts Relevantes ergeben. Teile das knapp und direkt mit."
        )

    if history:
        rendered = "\n".join(
            f"{'Nutzer' if role == 'user' else 'Antwort'}: {content}".strip()
            for role, content in history
            if content.strip()
        )
        body = f"Bisheriger Gesprächsverlauf:\n{rendered}\n\n{body}"

    return [
        {"role": "system", "content": str(values["synthesis_prompt"])},
        {"role": "user", "content": body},
    ]


async def run_query(db: Session, ctx: QueryContext) -> AsyncIterator[str]:
    """Drive the whole query and yield SSE frames as things happen."""
    values = ctx.values
    client = client_from_settings(values)
    queue: asyncio.Queue[str] = asyncio.Queue()

    async def emit(event: str, data: dict[str, Any]) -> None:
        await queue.put(_sse(event, data))

    runs: list[CollectionRun] = []
    started = time.perf_counter()

    async def orchestrate() -> None:
        try:
            await emit(
                "start",
                {
                    "collections": [
                        {"id": c.id, "name": c.name, "status": c.status} for c in ctx.collections
                    ],
                    "parallel": bool(values["parallel_collection_queries"]),
                },
            )

            if values["parallel_collection_queries"]:
                results = await asyncio.gather(
                    *[_run_collection(db, client, c, ctx, emit) for c in ctx.collections],
                    return_exceptions=False,
                )
                runs.extend(results)
            else:
                for collection in ctx.collections:
                    runs.append(await _run_collection(db, client, collection, ctx, emit))

            synthesis_text, trace = await _synthesize(client, ctx, runs, emit)
            await _persist(db, ctx, runs, synthesis_text, trace)

            await emit(
                "done",
                {
                    "total_ms": int((time.perf_counter() - started) * 1000),
                    "collections": [r.stats() for r in runs],
                    "synthesis": trace,
                },
            )
        except Exception as exc:  # noqa: BLE001 - report, never hang the stream
            logger.exception("Query fehlgeschlagen")
            await emit("error", {"error": str(exc)})
        finally:
            await queue.put("")

    task = asyncio.create_task(orchestrate())
    try:
        while True:
            frame = await queue.get()
            if frame == "":
                break
            yield frame
    finally:
        if not task.done():
            task.cancel()


async def _synthesize(
    client: SGLangClient, ctx: QueryContext, runs: list[CollectionRun], emit
) -> tuple[str, dict[str, Any]]:
    values = ctx.values
    trace: dict[str, Any] = {
        "skipped": False,
        "reasoning": "",
        "latency_ms": 0,
        "ttft_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }

    successful = [r for r in runs if not r.error]
    skip = (
        not values["synthesis_enabled"]
        or (values["skip_synthesis_single_collection"] and len(successful) <= 1)
    )

    if skip:
        text = successful[0].answer if successful else ""
        marker = str(values["no_hit_marker"])
        if successful and _is_no_hit(text, marker):
            text = "Dazu findet sich in den vorliegenden Dokumenten nichts."
        trace["skipped"] = True
        trace["reason"] = (
            "Synthese deaktiviert"
            if not values["synthesis_enabled"]
            else "Nur eine Sammlung — die Einzelantwort wird direkt ausgegeben."
        )
        await emit("synthesis_skipped", trace)
        if text:
            await emit("answer_delta", {"text": text})
        return text, trace

    messages = build_synthesis_messages(values, ctx.question, runs, ctx.history)
    await emit("synthesis_start", {"sources": len(successful)})

    answer = ""
    reasoning = ""
    try:
        async for chunk in client.chat_stream(
            messages=messages, **sampling_kwargs(values, step="synthesis")
        ):
            if chunk.kind == "reasoning":
                reasoning += chunk.text
                await emit("synthesis_reasoning", {"text": chunk.text})
            elif chunk.kind == "content":
                answer += chunk.text
                await emit("answer_delta", {"text": chunk.text})
            elif chunk.kind == "usage":
                tmp = CompletionResult()
                apply_usage(tmp, chunk.usage)
                trace["prompt_tokens"] = tmp.prompt_tokens
                trace["completion_tokens"] = tmp.completion_tokens
            elif chunk.kind == "done":
                trace["latency_ms"] = int(chunk.usage.get("latency_ms", 0))
                trace["ttft_ms"] = int(chunk.usage.get("ttft_ms", 0))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Synthese fehlgeschlagen")
        trace["error"] = str(exc)
        await emit("error", {"error": f"Synthese fehlgeschlagen: {exc}"})
        # Falling back to the raw findings beats returning nothing at all.
        answer = "\n\n".join(r.answer.strip() for r in successful if r.answer.strip())
        if answer:
            await emit("answer_delta", {"text": answer})

    trace["reasoning"] = reasoning
    return answer, trace


async def _persist(
    db: Session,
    ctx: QueryContext,
    runs: list[CollectionRun],
    answer: str,
    trace: dict[str, Any],
) -> None:
    message = Message(chat_id=ctx.chat_id, role="assistant", content=answer)
    db.add(message)
    db.flush()

    for run in runs:
        db.add(
            CollectionAnswer(
                message_id=message.id,
                collection_id=run.collection_id,
                collection_name=run.collection_name,
                answer_text=run.answer,
                reasoning_text=run.reasoning,
                latency_ms=run.latency_ms,
                ttft_ms=run.ttft_ms,
                prompt_tokens=run.prompt_tokens,
                cached_tokens=run.cached_tokens,
                completion_tokens=run.completion_tokens,
                cache_hit_rate=run.cache_hit_rate,
                error=run.error,
            )
        )

    db.add(
        SynthesisTrace(
            message_id=message.id,
            reasoning_text=trace.get("reasoning", ""),
            latency_ms=int(trace.get("latency_ms", 0)),
            ttft_ms=int(trace.get("ttft_ms", 0)),
            prompt_tokens=int(trace.get("prompt_tokens", 0)),
            completion_tokens=int(trace.get("completion_tokens", 0)),
            skipped=bool(trace.get("skipped", False)),
        )
    )

    chat = db.get(Chat, ctx.chat_id)
    if chat and chat.title == "Neuer Chat":
        chat.title = ctx.question.strip().split("\n")[0][:80] or "Neuer Chat"
    db.commit()


def prepare_context(
    db: Session, chat_id: str, question: str, collection_ids: list[str]
) -> QueryContext:
    values = settings_store.all_settings(db)
    collections = [
        c
        for c in (db.get(Collection, cid) for cid in collection_ids)
        if c is not None
    ]
    if not collections:
        raise ValueError("Es wurde keine gültige Sammlung ausgewählt.")
    return QueryContext(
        question=question,
        chat_id=chat_id,
        values=values,
        collections=collections,
        history=load_history(db, chat_id, int(values["history_turns"])),
    )
