#!/usr/bin/env python3
"""Prove that the second identical query hits the cache and is faster.

Acceptance criterion 3: a second identical query against the same collection
must show a measurably higher cache hit rate and a clearly lower TTFT than the
first. This script measures both and prints them side by side.

Method
------
1. ``POST /flush_cache`` so run 1 genuinely starts cold. (Skippable with
   ``--no-flush`` when you must not disturb a warm server.)
2. Run 1: the full prefix has to be prefilled — slow, hit rate near zero.
3. Run 2: byte-identical prefix, different question suffix — the image block
   comes out of the cache.
4. Run 3 (optional, ``--runs 3``): confirms run 2 was not a fluke.

Two independent measurements are reported per run because they can disagree, and
where they disagree that is itself informative:

* **cached_tokens / prompt_tokens** — reported by the server in
  ``usage.prompt_tokens_details.cached_tokens`` for that one request.
* **Δ /metrics** — the difference in ``sglang:cached_tokens_total`` and
  ``sglang:prompt_tokens_total`` across the request. Covers everything the
  server did in that window, so it can differ under concurrent load.

Usage
-----
    python bench/benchmark_cache.py --collection "Verträge"
    python bench/benchmark_cache.py --list
    python bench/benchmark_cache.py --collection-id abc123 --runs 3 --json report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import prefix as prefix_mod  # noqa: E402
from app import settings_store  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.ingest import instruction_for  # noqa: E402
from app.metrics import delta_hit_rate  # noqa: E402
from app.models import Collection  # noqa: E402
from app.sglang_client import client_from_settings  # noqa: E402

QUESTIONS = [
    "Nenne in einem Satz das Hauptthema.",
    "Welche Beträge werden genannt?",
    "Welche Daten und Fristen kommen vor?",
    "Wer sind die beteiligten Parteien?",
    "Welche Tabellen sind enthalten?",
]


def resolve_collection(db, name: str | None, collection_id: str | None) -> Collection:
    if collection_id:
        collection = db.get(Collection, collection_id)
        if collection is None:
            raise SystemExit(f"Keine Sammlung mit der ID {collection_id!r}.")
        return collection
    matches = [c for c in db.query(Collection).all() if c.name == name]
    if not matches:
        available = ", ".join(c.name for c in db.query(Collection).all()) or "(keine)"
        raise SystemExit(f"Keine Sammlung namens {name!r}. Vorhanden: {available}")
    return matches[0]


async def one_run(client, messages, values, question: str, label: str) -> dict:
    before = await client.metrics_safe()

    started = time.perf_counter()
    first_token_at: float | None = None
    content = ""
    usage: dict = {}

    payload_messages = list(messages)
    payload_messages[1] = {
        **messages[1],
        "content": messages[1]["content"][:-1] + [{"type": "text", "text": f"Frage: {question}"}],
    }

    async for chunk in client.chat_stream(
        messages=payload_messages,
        model=str(values["sglang_model"]),
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        top_k=-1,
        repetition_penalty=1.0,
        stop=[],
        thinking=False,
    ):
        if chunk.kind in ("content", "reasoning") and first_token_at is None:
            first_token_at = time.perf_counter()
        if chunk.kind == "content":
            content += chunk.text
        elif chunk.kind == "usage":
            usage = chunk.usage

    total_ms = (time.perf_counter() - started) * 1000
    ttft_ms = ((first_token_at or time.perf_counter()) - started) * 1000

    after = await client.metrics_safe()

    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    cached_tokens = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)

    return {
        "label": label,
        "question": question,
        "ttft_ms": round(ttft_ms, 1),
        "total_ms": round(total_ms, 1),
        "prompt_tokens": prompt_tokens,
        "cached_tokens": cached_tokens,
        "hit_rate": round(cached_tokens / prompt_tokens, 4) if prompt_tokens else 0.0,
        "server_delta_hit_rate": (
            round(rate, 4)
            if before and after and (rate := delta_hit_rate(before, after)) is not None
            else None
        ),
        "answer_preview": content.strip()[:80],
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--collection", help="Name der Sammlung")
    parser.add_argument("--collection-id", help="ID der Sammlung (eindeutiger)")
    parser.add_argument("--runs", type=int, default=2, help="Anzahl Durchläufe (Default 2)")
    parser.add_argument("--no-flush", action="store_true", help="Cache vorher nicht leeren")
    parser.add_argument("--list", action="store_true", help="Sammlungen auflisten und beenden")
    parser.add_argument("--json", help="Ergebnis zusätzlich als JSON hierhin schreiben")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        if args.list:
            print(f"{'ID':<34} {'Status':<10} {'Seiten':>7} {'Tokens':>10}  Name")
            for collection in db.query(Collection).all():
                print(
                    f"{collection.id:<34} {collection.status:<10} "
                    f"{len(collection.pages):>7} {collection.token_count:>10}  {collection.name}"
                )
            return 0

        if not args.collection and not args.collection_id:
            parser.error("--collection oder --collection-id angeben (oder --list)")

        collection = resolve_collection(db, args.collection, args.collection_id)
        values = settings_store.all_settings(db)
        client = client_from_settings(values)

        page_refs = prefix_mod.load_page_refs(db, collection.id)
        if not page_refs:
            raise SystemExit("Die Sammlung enthält keine Seiten.")

        messages = prefix_mod.build_messages(
            system_prompt=str(values["global_system_prompt"]),
            page_refs=page_refs,
            collection_name=collection.name,
            collection_instruction_template=str(values["collection_instruction"]),
            label_template=str(values["page_label_template"]),
            question="Platzhalter",
        )

        health = await client.health()
        if not health["ok"]:
            raise SystemExit(f"SGLang nicht erreichbar: {health['error']}")

        print("=" * 78)
        print(f"  Sammlung   {collection.name}")
        print(f"  Status     {collection.status}")
        print(f"  Umfang     {len(page_refs)} Seiten · ~{collection.token_count:,} geschätzte Tokens")
        print(f"  Server     {values['sglang_base_url']} ({health['latency_ms']} ms)")
        print("=" * 78)

        if not args.no_flush:
            print("\nLeere den Server-Cache, damit Lauf 1 wirklich kalt startet …")
            result = await client.flush_cache(30)
            print(f"  → {result['message'] or result['status_code']}")
            await asyncio.sleep(2)

        runs: list[dict] = []
        for index in range(args.runs):
            label = "kalt" if index == 0 else f"warm #{index}"
            question = QUESTIONS[index % len(QUESTIONS)]
            print(f"\nLauf {index + 1}/{args.runs} ({label}) — läuft …", flush=True)
            run = await one_run(client, messages, values, question, label)
            runs.append(run)
            print(
                f"  TTFT {run['ttft_ms'] / 1000:7.2f} s   "
                f"gesamt {run['total_ms'] / 1000:7.2f} s   "
                f"Prompt {run['prompt_tokens']:>8,}   "
                f"gecacht {run['cached_tokens']:>8,}   "
                f"Trefferquote {run['hit_rate'] * 100:5.1f} %"
            )

        print("\n" + "=" * 78)
        print("  ERGEBNIS")
        print("=" * 78)
        header = f"  {'Lauf':<9} {'TTFT':>10} {'Gesamt':>10} {'Prompt-Tok':>12} {'gecacht':>10} {'Treffer':>9} {'Δ/metrics':>11}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for run in runs:
            delta = (
                f"{run['server_delta_hit_rate'] * 100:.1f} %"
                if run["server_delta_hit_rate"] is not None
                else "n. v."
            )
            print(
                f"  {run['label']:<9} {run['ttft_ms'] / 1000:>9.2f}s {run['total_ms'] / 1000:>9.2f}s "
                f"{run['prompt_tokens']:>12,} {run['cached_tokens']:>10,} "
                f"{run['hit_rate'] * 100:>8.1f}% {delta:>11}"
            )

        cold, warm = runs[0], runs[1:]
        if warm:
            warm_ttft = statistics.mean(r["ttft_ms"] for r in warm)
            warm_rate = statistics.mean(r["hit_rate"] for r in warm)
            speedup = cold["ttft_ms"] / warm_ttft if warm_ttft else float("inf")

            print("\n  Vergleich kalt gegen warm")
            print(f"    TTFT             {cold['ttft_ms'] / 1000:.2f} s → {warm_ttft / 1000:.2f} s   ({speedup:.1f}× schneller)")
            print(f"    Trefferquote     {cold['hit_rate'] * 100:.1f} % → {warm_rate * 100:.1f} %")

            rate_ok = warm_rate > cold["hit_rate"] and warm_rate >= 0.5
            ttft_ok = warm_ttft < cold["ttft_ms"] * 0.8
            print()
            print(f"    [{'BESTANDEN' if rate_ok else 'FEHLGESCHLAGEN'}] Trefferquote deutlich höher und ≥ 50 %")
            print(f"    [{'BESTANDEN' if ttft_ok else 'FEHLGESCHLAGEN'}] TTFT mindestens 20 % niedriger")

            if not (rate_ok and ttft_ok):
                print(
                    "\n  Wenn das fehlschlägt: läuft die Instanz mit --enable-hierarchical-cache?\n"
                    "  Ist die Sammlung als 'cached' markiert? Weicht /get_server_info von den\n"
                    "  Settings ab? Siehe docs/vastai-setup.md §6."
                )

        if args.json:
            Path(args.json).write_text(
                json.dumps(
                    {
                        "collection": {
                            "id": collection.id,
                            "name": collection.name,
                            "pages": len(page_refs),
                            "status": collection.status,
                        },
                        "runs": runs,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"\n  JSON-Report geschrieben: {args.json}")

        if warm:
            return 0 if (rate_ok and ttft_ok) else 1
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
