# Hermes

**Visuelles CAG-System** (Cache-Augmented Generation) auf Basis von SGLang HiCache.

Ganze Dokumente werden als **Bilder** in den Prompt vorangestellt und ihr KV-Cache
über SGLangs HiRadixTree dauerhaft vorgehalten. Eine Frage kostet dann nur noch
das Prefill der Frage selbst — der Bildpräfix liegt bereits im Cache.

**Kein RAG.** Keine Embeddings, kein Vektorindex, kein Chunk-Retrieval. Die
Dokumente gehen als Bilder ins Modell, damit Layout, Tabellen und Abbildungen
erhalten bleiben.

---

## Schnellstart

```bash
cp .env.example .env
$EDITOR .env          # SGLANG_BASE_URL und SGLANG_WEB_PASSWORD eintragen
docker compose up
```

→ UI unter <http://localhost:8080>

Kein Migrationsschritt, kein Seeding von Hand: Schema und Settings werden beim
Start angelegt.

Das Sprachmodell läuft **nicht** in diesem Stack, sondern extern auf einer
vast.ai-GPU hinter einem Cloudflare Tunnel. Aufsetzen:
**[docs/vastai-setup.md](docs/vastai-setup.md)**.

Entwicklung mit Hot Reload:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up   # UI auf :5173
```

Monitoring-Profil (Prometheus + Grafana):

```bash
docker compose --profile monitoring up
```

---

## Das Grundprinzip

**Ein Cache-Treffer entsteht nur bei byte-identischem Präfix.** Alles in diesem
System ist darauf ausgerichtet, dass der Präfix einer Sammlung bei jeder Anfrage
bitgenau reproduziert wird. Jede Abweichung — ein Zeitstempel, eine andere
Bild-Encodierung, eine andere Reihenfolge, ein variabler System-Prompt —
zerstört den Treffer und macht das System wertlos.

```
messages[0] system : globaler System-Prompt      ← identisch für ALLE Sammlungen
messages[1] user   : Label, Bild, Label, Bild …  ← der gecachte Block
                     Sammlungs-Instruktion       ← NACH den Bildern
                     Frage (+ Verlauf)           ← der einzige variable Teil
```

Durchgesetzt wird das an vier Stellen:

- **`backend/app/prefix.py` ist der einzige Prompt-Builder.** Ingest und Query
  rufen dieselbe Funktion. Es gibt keinen zweiten Pfad, auf dem ein Byte
  abweichen könnte.
- **Harte Guards vor jedem Versand.** Zusätzliche Nachrichten, ein veränderter
  System-Prompt, ein Zeitstempel oder eine UUID vor dem Bildblock ⇒
  `PrefixViolation`, der Request geht nicht raus.
- **Base64 wird einmal beim Ingest geschrieben** und danach byte-identisch
  gelesen. Nie neu encodiert.
- **`prefix_hash`** (SHA-256 über die Präfix-Bytes) wird bei jedem Query
  verglichen. Weicht er ab, wird die Sammlung sofort als `stale` markiert und
  die UI sagt es — statt still einen Cache-Miss als Treffer auszugeben.

### Append-only

| Operation | Wirkung |
|---|---|
| Dokument **hinten** anhängen | Präfix bleibt gültig, nur der neue Teil wird gewärmt |
| **letztes** Dokument entfernen | echte Verkürzung — bleibt gecacht |
| Dokument aus der **Mitte** entfernen | `stale`, kompletter Re-Ingest, UI bestätigt mit Seiten- und Tokenzahl |
| Dokumente umsortieren | `stale` |
| DPI/Format/Graustufen ändern | `stale` |
| globalen System-Prompt ändern | **alle** Sammlungen `stale` |

### Ingest ≠ Query

Zwei getrennte Operationen, getrennte Module, getrennte Aktionen in der UI.
Während eines Queries wird **niemals** ingestiert.

**Ingest** rendert die Seiten, persistiert die Base64-Bytes und sendet den
kompletten Präfix einmal mit `max_tokens: 1`, `temperature: 0`, Thinking aus.
Die Antwort wird verworfen — es geht nur um L1/L2/L3.

**Query** nutzt denselben Präfix und hängt nur die Frage an. Bei *n* angehakten
Sammlungen werden *n* Requests gesendet (standardmäßig **sequenziell**, weil
parallele Requests sich gegenseitig aus dem GPU-Pool verdrängen), deren
Teilantworten anschließend zu einer Antwort synthetisiert werden.

---

## Funktionsumfang

- **Sammlungen** als Äste im HiRadixTree, strikt append-only, mit unveränderlicher
  `sequence_index` je Seite als einziger Ordnungsquelle.
- **PDF → Bild** über PyMuPDF, DPI global / pro Sammlung / pro Upload einstellbar,
  mit Live-Vorschau von Pixelmaßen und geschätzter Tokenzahl am Slider.
- **Bildvorschau** als Vollbild-Lightbox mit Zoom bis 8×, Pan, Seitennavigation
  und den echten Metadaten. Gezeigt wird exakt die Datei, die auch an das Modell
  geht — keine separat generierte Vorschau.
- **Token-Budget** aus `262144 − reserved_output_tokens − prompt_overhead`, mit
  farblicher Warnung ab 75 % und hartem Block für Ingests, die nicht passen
  (inklusive Vorschlag, welche DPI wieder hineinpasst).
- **Streaming** über SSE, Thinking getrennt vom Antworttext geparst
  (`reasoning_content`, dank `--reasoning-parser qwen3`).
- **Pro Nachricht ausklappbar**: jede Einzelantwort mit ihrem Thinking-Block und
  ihren Kennzahlen, plus der Thinking-Block der Synthese. Standardmäßig
  eingeklappt — sichtbar ist zunächst nur die fertige Antwort.
- **Cache-Baum** mit Live-Metriken, Knoten proportional zur Tokenzahl, eingefärbt
  nach vermutetem Cache-Level. Was gemessen und was abgeleitet ist, steht dran.
- **Settings** für jeden Serverparameter, gruppiert und erklärt, inklusive
  generiertem vast.ai-Launch-Kommando und Abgleich mit der laufenden Instanz.

---

## Aufbau

```
backend/app/
  prefix.py         DER Prompt-Builder + Invarianten-Guards + prefix_hash
  tokens.py         Qwen-VL smart_resize + Vision-Token-Schätzung
  rendering.py      PDF/Bild → PNG/JPEG, Downscale, Graustufen, Base64
  ingest.py         Render- und Warmup-Pipeline mit Fortschritt
  query.py          n Sammlungs-Requests + Synthese, SSE
  sglang_client.py  gekapselter SGLang-Zugriff — eine Stelle für API-Drift
  metrics.py        Prometheus-Text → JSON, Präfix-Normalisierung
  cache_tree.py     Baum aus Metadaten + Metriken + gemessene Trefferquoten
frontend/src/
  lib/streamingMarkdown.ts   Stream-Stabilisierung gegen Flackern
  components/CacheTreeView   D3-Baum + Dashboard
  components/Lightbox        Vollbildvorschau mit Zoom/Pan
```

Details und die Begründung der Designentscheidungen: **[ARCHITECTURE.md](ARCHITECTURE.md)**.

---

## Tests

```bash
# Backend — Präfix-Determinismus, Token-Schätzung, Metrik-Parsing, E2E
cd backend && pip install -r requirements-dev.txt && python -m pytest tests/ -q

# Frontend — Markdown-Torture-Dokument und Streaming-Stabilität
cd frontend && npm install && npm test
```

Die wichtigsten sind die in `backend/tests/test_prefix_determinism.py`: Wenn dort
etwas fehlschlägt, gibt es keine Cache-Treffer mehr und das Produkt ist wertlos.

## Benchmark

Belegt, dass die zweite identische Anfrage messbar besser cacht und schneller
antwortet:

```bash
python bench/benchmark_cache.py --list
python bench/benchmark_cache.py --collection "Verträge" --runs 3
```

Ausgegeben werden pro Lauf TTFT, Gesamtdauer, Prompt-Tokens, davon gecacht, und
die Trefferquote aus zwei unabhängigen Quellen (der `usage` der Antwort und dem
Delta über `/metrics`).

---

## Bekannte Grenzen

**SGLang bietet keinen Endpunkt zum Auslesen des Radix-Trees.** Geprüft gegen
die vollständige Routenliste von `entrypoints/http_server.py`. Die Visualisierung
rekonstruiert die Struktur deshalb aus den eigenen Ingest-Metadaten — sie
entspricht der Präfix-Struktur, die im Server entstehen *muss*, aber die
tatsächlichen Knoten des Servers sehen wir nie. Die UI beschriftet jeden Wert als
*gemessen* oder *abgeleitet*, statt eine Präzision vorzutäuschen, die es nicht gibt.

**Das Cache-Level (L1/L2/L3) ist eine Heuristik.** Verdrängung aus dem GPU-Pool
ist von außen nicht beobachtbar. Die ehrlichste verfügbare Aussage ist die
gemessene Trefferquote der letzten Anfrage — die steht deshalb überall daneben.

**Die Tokenschätzung ist eine Schätzung.** Für Bilder ist sie exakt (die
Geometrie ist deterministisch), für Text bewusst grob: ein mitgelieferter
Tokenizer müsste dem des Servers exakt entsprechen, um mehr wert zu sein als
eine Abschätzung, und eine Abweichung wäre schlimmer als eine offen konservative
Schätzung.

**Vision-Token-Raster: 32×32 px, nicht 28×28.** `Qwen/Qwen3.6-27B` nutzt
`patch_size: 16` und `merge_size: 2`. Die häufig zitierten 28 px stammen von
Qwen2-VL/Qwen2.5-VL (`patch_size: 14`). Die Werte sind Settings — ein
Modellwechsel ist eine Konfigurations-, keine Codeänderung.
