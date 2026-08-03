# Hermes — Architektur & Vorgehen

Visuelles **CAG**-System (Cache-Augmented Generation) auf Basis von SGLang HiCache.
Ganze Dokumente werden als **Bilder** in den Prompt vorangestellt; ihr KV-Cache lebt
dauerhaft im HiRadixTree (L1 GPU → L2 Host-RAM → L3 Storage). Eine Frage kostet dann
nur noch das Prefill der Frage selbst.

**Kein RAG.** Keine Embeddings, kein Vektorindex, kein Chunk-Retrieval, kein
Textextraktions-Ersatzpfad.

---

## 0. Ergebnis der Vorab-Verifikation gegen SGLang `main`

Vor der Implementierung wurden alle Flags, Endpunkte und Metriknamen gegen die
aktuelle SGLang-Quelle geprüft (`python/sglang/srt/server_args.py`,
`entrypoints/http_server.py`, `entrypoints/openai/protocol.py`,
`parser/reasoning_parser.py`) statt gegen Erinnerung.

### 0.1 Bestätigt

| Gegenstand | Status |
|---|---|
| `--enable-hierarchical-cache` | ✅ existiert, Default `False` |
| `--hicache-ratio` | ✅ float, Default `2.0` |
| `--hicache-size` | ✅ int GB, überschreibt `hicache-ratio` |
| `--hicache-write-policy` | ✅ `write_back \| write_through \| write_through_selective`, Default `write_through` |
| `--hicache-io-backend` | ✅ `direct \| kernel \| kernel_ascend`, Default `kernel` |
| `--hicache-mem-layout` | ✅ `layer_first \| page_first \| page_first_direct \| page_first_kv_split \| page_head`, Default `page_first` |
| `--hicache-storage-backend` | ✅ `file \| mooncake \| hf3fs \| nixl \| aibrix \| dynamic \| eic \| simm \| mori \| shm`, Default `None` |
| `--hicache-storage-prefetch-policy` | ✅ `best_effort \| wait_complete \| timeout`, **Default `timeout`** (nicht `best_effort`) |
| `--reasoning-parser qwen3` | ✅ Key `qwen3` existiert in `ReasoningParser.DetectorMap` |
| `--api-key` → `Authorization: Bearer <key>` | ✅ bestätigt in `http_server.py` |
| `/flush_cache?timeout=<float>` | ✅ `GET`/`POST`, `timeout` ist ein Query-Param `>= 0.0` |
| `/get_server_info`, `/health`, `/metrics` | ✅ vorhanden |
| Metriken `sglang:cache_hit_rate`, `sglang:hicache_host_used_tokens`, `sglang:hicache_host_total_tokens`, `sglang:time_to_first_token_seconds`, `sglang:token_usage`, `sglang:num_used_tokens`, `sglang:max_total_num_tokens`, `sglang:cached_tokens_total`, `sglang:prompt_tokens_total` | ✅ vorhanden |
| `usage.prompt_tokens_details.cached_tokens` in der OpenAI-Antwort | ✅ vorhanden |
| `separate_reasoning` (Default `true`) und `chat_template_kwargs` im Chat-Request | ✅ vorhanden |
| `nvidia/Qwen3.6-27B-NVFP4` auf HF | ✅ existiert (Basis: `Qwen/Qwen3.6-27B`) |

### 0.2 Abweichungen von der Spezifikation — bewusst und begründet

Diese Punkte weichen von der Aufgabenstellung ab, weil die Aufgabenstellung an
dieser Stelle nachweislich veraltet oder technisch nicht umsetzbar ist.

**A) Vision-Token-Raster ist 32×32 px, nicht 28×28 px.**
Die Spec nennt „Patches von 28×28 Pixeln, mit Merging". Das ist der Wert von
Qwen2-VL / Qwen2.5-VL (`patch_size: 14`, `merge_size: 2`). Die
`preprocessor_config.json` von `Qwen/Qwen3.6-27B` sagt aber:

```json
{ "patch_size": 16, "merge_size": 2,
  "size": { "shortest_edge": 65536, "longest_edge": 16777216 } }
```

⇒ ein Vision-Token deckt `16 × 2 = 32` px Kantenlänge ab, also **32×32 px**.
Zusätzlich gilt eine Flächenklammer von `65 536` bis `16 777 216` Pixeln
(entspricht 256×256 bis 4096×4096) mit seitenverhältnis-erhaltender Skalierung.
Der Schätzer in `backend/app/tokens.py` implementiert `smart_resize` mit genau
diesen Parametern — und macht sie in den Settings konfigurierbar
(`vision_patch_size`, `vision_merge_size`, `vision_min_pixels`,
`vision_max_pixels`), damit ein Modellwechsel keine Codeänderung braucht.

**B) SGLang bietet keinen Endpunkt zum Dumpen des Radix-Trees.**
Geprüft wurde die vollständige Routenliste von `http_server.py`. Es gibt
`/get_server_info`, `/flush_cache`, `/hicache/storage-backend` (GET/PUT/DELETE,
nur Metadaten des Backends) und ein optionales `/dumper/{method}` (nur aktiv bei
gesetztem Debug-Dumper, kein Radix-Tree). **Kein** Tree-Dump.
⇒ Die Visualisierung wird wie in der Spec als Fallback beschrieben aufgebaut:
Struktur aus eigenen Ingest-Metadaten rekonstruiert, Zustand aus `/metrics`
angereichert, Trefferquote aus tatsächlich gemessenen Queries. Die UI beschriftet
jeden Knoten explizit als *gemessen* oder *abgeleitet*.

**C) `--hicache-storage-prefetch-policy` Default ist `timeout`, nicht wie oft
zitiert `best_effort`.** Wir setzen explizit `wait_complete`, wie von der Spec
gefordert und aus demselben Grund: ein Teiltreffer auf einem 200k-Token-Präfix
zerstört den Nutzen.

**D) Metrik-Präfix ist versionsabhängig.** Ältere SGLang-Versionen exportieren
`sglang:cache_hit_rate`, neuere (ab ~v0.5.4) `sglang_cache_hit_rate`. Der Parser
in `backend/app/metrics.py` normalisiert beide Schreibweisen auf einen
kanonischen Namen, statt sich auf eine festzulegen.

**E) `nvidia/Qwen3.6-27B-NVFP4` ist auf HF mit `pipeline_tag: text-generation`
markiert**, obwohl das Basismodell `Qwen/Qwen3.6-27B` `image-text-to-text` ist.
Das ist bei ModelOpt-Quantisierungen üblich (nur der Sprach-Teil wird
quantisiert, der Vision-Tower bleibt), aber es ist nicht garantiert, dass der
Vision-Tower im Checkpoint liegt. `docs/vastai-setup.md` enthält deshalb einen
expliziten Verifikationsschritt (`/get_model_info` + ein Ein-Bild-Smoke-Test)
und das unquantisierte Basismodell als dokumentierte Rückfalloption.

---

## 1. Fundamentales Prinzip: Byte-identischer Präfix

Ein Cache-Treffer entsteht **nur** bei byte-identischem Präfix. Das gesamte
System ist darauf ausgerichtet. Konkret durchgesetzt durch:

1. **`backend/app/prefix.py` ist die einzige Stelle im Code, die einen
   Prompt zusammenbaut.** Ingest und Query rufen dieselbe Funktion
   `build_prefix(collection)` auf. Es gibt keinen zweiten Pfad.
2. **Harte Guards.** `assert_prefix_invariants()` prüft vor jedem Versand:
   - Element 0 ist die System-Nachricht und exakt gleich dem globalen
     System-Prompt (kein Timestamp, kein Datum, keine Sammlungs-ID).
   - Vor dem letzten Textblock der User-Nachricht liegen **ausschließlich**
     Bildteile und die dazugehörigen fixen Seitenlabels.
   - Kein Chat-Verlauf, keine variable Zeichenkette, kein `strftime`-Ergebnis
     erscheint vor dem Bildblock.
   - `sequence_index` der Seiten ist lückenlos aufsteigend ab 0.
   Verletzung ⇒ `PrefixViolation`, Request wird nicht abgesetzt.
3. **Base64 wird einmal beim Ingest erzeugt und auf Platte persistiert**
   (`pages/<id>.b64`). Query liest exakt diese Bytes. Es wird nie neu encodiert,
   nie neu komprimiert, nie durch eine Bibliothek geschleust, die
   Zeilenumbrüche oder Padding anders setzt.
4. **`prefix_hash`** = SHA-256 über die kanonische JSON-Serialisierung des
   Präfixes (ohne Frage). Wird bei Ingest gespeichert und bei jedem Query neu
   berechnet und verglichen. Abweichung ⇒ Sammlung wird sofort auf `stale`
   gesetzt und die UI zeigt es an, statt still einen Cache-Miss zu produzieren.
5. **Reihenfolge kommt ausschließlich aus `Page.sequence_index`** — nie aus
   Dateinamen, `created_at` oder der DB-Default-Sortierung. Jede Query gegen
   Pages hat ein explizites `ORDER BY sequence_index`.

### 1.1 Aufbau des Präfixes

```
messages[0] = { role: "system", content: <GLOBAL_SYSTEM_PROMPT> }   # identisch für ALLE Sammlungen
messages[1] = { role: "user", content: [
    {type:"text",      text: "<<< DOKUMENT: rechnungen.pdf | SEITE 1/12 | #0 >>>"},
    {type:"image_url", image_url:{url:"data:image/png;base64,…"}},   # Bytes vom Ingest
    {type:"text",      text: "<<< DOKUMENT: rechnungen.pdf | SEITE 2/12 | #1 >>>"},
    {type:"image_url", image_url:{url:"data:image/png;base64,…"}},
    …                                                                # ── Ende Bildblock ──
    {type:"text",      text: <COLLECTION_INSTRUCTION>},              # NACH den Bildern
    {type:"text",      text: <FRAGE + optionaler Verlauf>}           # einziger variabler Teil
]}
```

Der globale System-Prompt steht ganz vorne und ist für alle Sammlungen gleich ⇒
gemeinsamer Wurzel-Präfix im HiRadixTree, keine Cache-Barriere.
Die Sammlungs-Instruktion steht **nach** dem Bildblock.
Chat-Verlauf gehört nie vor die Bilder — er wird hinter den Bildblock gehängt
bzw. nur im Synthese-Schritt verwendet.

Die Seitenlabels sind Teil des Präfixes (deterministisch, aus unveränderlichen
Feldern). Sie sind nötig, damit das Modell Fundstellen mit Dokumentname und
Seite belegen kann. Ihr Template ist ein Setting; die UI warnt, dass eine
Änderung alle Caches invalidiert.

### 1.2 Append-only als Cache-Erhaltungsstrategie

| Operation | Wirkung auf den Ast |
|---|---|
| Dokument **hinten** anhängen | Präfix bleibt gültig → Status `cached`, nur der neue Teil wird ingestiert |
| **letztes** Dokument entfernen | Präfix bleibt gültig (echte Verkürzung) → `cached`, Tokenzahl sinkt |
| Dokument aus der **Mitte** entfernen | Ast bricht → `stale`, kompletter Re-Ingest, UI bestätigt mit Seiten-/Tokenzahl |
| Dokumente **umsortieren** | `stale` |
| **DPI/Format/Graustufen** ändern | Bilder ändern sich → `stale` |
| **Globaler System-Prompt** oder Label-Template ändern | **alle** Sammlungen `stale`, UI warnt deutlich |

---

## 2. Ingest ≠ Query

Zwei getrennte Operationen, im Code getrennte Module, in der UI getrennte
Aktionen. Während eines Queries wird **niemals** ingestiert.

**Ingest (Cache-Warmup)** — `backend/app/ingest.py`
1. Dokument → Bilder rendern (PyMuPDF `page.get_pixmap(dpi=…)`).
2. Base64 einmalig persistieren — genau diese Bytes werden wiederverwendet.
3. Ein Request an SGLang mit dem **kompletten aktuellen Präfix**,
   `max_tokens: 1`, `temperature: 0`, Thinking aus. Antwort wird verworfen.
4. Status persistieren: `pending | ingesting | cached | stale | error`,
   inklusive `cached_prefix_token_count` und `prefix_hash`.

**Query** — `backend/app/query.py`
Nutzt denselben Präfix, hängt nur die Frage an.

---

## 3. Query-Ablauf

1. Nutzer hakt *n* Sammlungen an und stellt eine Frage.
2. *n* separate Requests, einer pro Sammlung:
   `[System] + [Bilder der Sammlung] + [Sammlungs-Instruktion] + [Frage]`.
3. HiRadixTree matcht den Bild-Präfix ⇒ nur die Frage muss geprefillt werden.
4. Die *n* Teilantworten gehen in einen **Synthese-Request**.
5. **Standard: sequenziell.** Parallele Requests verdrängen sich gegenseitig aus
   dem GPU-Pool (L1) und erzwingen teure L2/L3-Rückholungen. Setting
   `parallel_collection_queries` (Default: aus).
6. Alle Requests streamen; Teilantworten erscheinen live via SSE.
7. Pro Sammlung werden `prompt_tokens`, `cached_tokens`, daraus
   `cache_hit_rate = cached/prompt`, sowie TTFT und Latenz gemessen und
   persistiert (`CollectionAnswer`). Zusätzlich wird `/metrics` vor und nach dem
   Query gesampelt, um die serverseitige Hit-Rate zu delta-bilden.

---

## 4. Token-Budget

- Nativer Kontext: **262 144** Token, soll voll ausgenutzt werden.
- Budget je Sammlung: `262144 − reserved_output_tokens − prompt_overhead`.
  `reserved_output_tokens` ist einstellbar; `prompt_overhead` wird aus
  System-Prompt + Instruktion + Labels + Chat-Template-Aufschlag berechnet.
- Die UI zeigt pro Sammlung Seitenzahl, geschätzte Tokens und Prozent des
  Fensters, färbt ab 75 % gelb und ab 90 % rot.
- Ein Ingest, der das Budget überschreiten würde, wird **blockiert** (HTTP 409)
  mit konkreter Fehlermeldung: aktuelle Tokens, Budget, Überschuss, und dem
  Hinweis, welche DPI das Dokument wieder hineinbringen würde.

---

## 5. Module

```
backend/app/
  config.py         .env → Settings-Defaults
  db.py             Engine, Session, Auto-Migration beim Start
  models.py         Collection, Document, Page, Chat, Message,
                    CollectionAnswer, SynthesisTrace, Setting
  settings_store.py Key-Value-Settings mit Typisierung + .env-Vorbelegung
  prompts.py        die drei versionierten Prompt-Templates
  tokens.py         Qwen-VL smart_resize + Vision-Token-Schätzung
  rendering.py      PDF/Bild → PNG/JPEG, Downscale, Graustufen, Base64
  prefix.py         DER Prompt-Builder + Invarianten-Guards + prefix_hash
  sglang_client.py  httpx-Client: /health, /get_server_info, /metrics,
                    /flush_cache, /v1/chat/completions (Stream + Reasoning)
  metrics.py        Prometheus-Text → JSON, Präfix-Normalisierung
  ingest.py         Render- und Warmup-Pipeline mit Fortschritt
  query.py          Orchestrierung: n Collection-Queries + Synthese, SSE
  cache_tree.py     Baum aus Metadaten + Metriken + gemessene Hit-Raten
  api/*.py          FastAPI-Router
```

**Warum FastAPI + SQLite + SQLAlchemy?** Vorschlag der Spec übernommen. Kein
Multi-User, ein Schreiber, WAL-Modus reicht vollkommen; keine externe DB als
zusätzlicher Compose-Service nötig. Streaming über **SSE** statt WebSocket:
unidirektional, überlebt nginx-Proxying ohne Sonderkonfiguration, trivial
wiederverbindbar.

**Frontend:** React + Vite + TypeScript + Tailwind, dunkel, hohe
Informationsdichte, ruhige Typografie. `react-markdown` + `remark-gfm` +
`remark-math`/`rehype-katex`, Syntax-Highlighting mit Copy-Button, und ein
**Stream-Puffer**, der unvollständige Codefences/Tabellen zurückhält, bis sie
schließbar sind — gegen das Flackern und die halb gerenderten Tabellen der
Vorversion. Snapshot-Tests gegen ein Markdown-Torture-Dokument.

---

## 6. Reihenfolge der Umsetzung

1. Datenmodell + Settings + Token-Schätzer + `prefix.py` (mit Tests zuerst)
2. Render-/Ingest-Pipeline
3. SGLang-Client + Metrics-Parser
4. Query-Orchestrierung + SSE
5. REST-API
6. Frontend
7. HiRadixTree-Visualisierung
8. Docker Compose, Doku, Benchmark
