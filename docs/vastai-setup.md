# SGLang auf vast.ai einrichten

Das Sprachmodell läuft **nicht** im Docker-Compose dieses Projekts, sondern auf
einer gemieteten vast.ai-GPU. Das lokale System spricht sie über einen
Cloudflare Tunnel an, abgesichert mit einem `WEB_PASSWORD`.

Alle Flags in diesem Dokument sind gegen `python/sglang/srt/server_args.py` auf
`main` verifiziert (Stand: siehe `ARCHITECTURE.md` §0). Die SGLang-API ändert
sich schnell — prüfe die Flags gegen die Version, die dein Image tatsächlich
installiert, bevor du sie übernimmst:

```bash
python3 -m sglang.launch_server --help | grep hicache
```

---

## 1. Instanz mieten

| Anforderung | Empfehlung |
|---|---|
| GPU | ≥ 48 GB VRAM (RTX 6000 Ada, L40S, A100 80G, H100). NVFP4 braucht Blackwell/Hopper mit ModelOpt-Support — bei Ampere stattdessen das unquantisierte Basismodell nehmen. |
| Host-RAM | Mindestens `hicache_ratio × KV-Pool-Größe`. Bei `--hicache-ratio 2` also grob das Doppelte des GPU-KV-Pools. Für dieses System eher großzügig: der L2-Pool ist der Grund, warum ein Bildpräfix einen Tab-Wechsel überlebt. |
| Disk | Ausreichend für L3 (`--hicache-storage-backend file`). 200 GB ist ein vernünftiger Start; ein 200k-Token-Präfix belegt je nach Modellgröße mehrere GB. |
| Ports | 30000 (SGLang), ggf. 8080 für den Tunnel. |

## 2. Startkommando

```bash
export WEB_PASSWORD="ein-langes-zufaelliges-passwort"

python3 -m sglang.launch_server \
  --model-path nvidia/Qwen3.6-27B-NVFP4 \
  --quantization modelopt_fp4 \
  --reasoning-parser qwen3 \
  --context-length 262144 \
  --host 0.0.0.0 --port 30000 \
  --api-key "$WEB_PASSWORD" \
  --page-size 64 \
  --mem-fraction-static 0.85 \
  --enable-hierarchical-cache \
  --hicache-ratio 2 \
  --hicache-write-policy write_through \
  --hicache-storage-backend file \
  --hicache-storage-prefetch-policy wait_complete \
  --hicache-io-backend direct \
  --enable-metrics
```

> Die Settings-Seite der UI erzeugt genau dieses Kommando aus den dort
> eingestellten Werten, zum Kopieren. Beim Verbinden gleicht sie es über
> `/get_server_info` mit der tatsächlich laufenden Instanz ab und zeigt
> Abweichungen an — die HiCache-Flags sind Startparameter, sie lassen sich zur
> Laufzeit nicht ändern.

### Was die Flags bedeuten

**`--enable-hierarchical-cache`**
Aktiviert die dreistufige Hierarchie **L1 (GPU-VRAM) → L2 (Host-RAM) → L3
(Storage)**. Ohne dieses Flag gibt es nur den normalen RadixAttention-Cache im
VRAM, und ein 200k-Token-Bildpräfix wird beim ersten Speicherdruck verworfen.
Für dieses Projekt ist das Flag nicht optional.

**`--hicache-ratio 2`**
Verhältnis der Größe des Host-KV-Pools zum Device-Pool. `2` bedeutet: der
Host-Pool ist doppelt so groß wie der GPU-Pool. Alternativ setzt
`--hicache-size <GB>` die Größe absolut und überschreibt die Ratio.
*(Default: `2.0`)*

**`--hicache-write-policy write_through`**
Schreibt jeden Zugriff sofort in die nächste Stufe durch. **Genau das ist hier
gewollt**: die Bild-Präfixe sollen dauerhaft bis nach L3 durchgeschrieben werden
und dort auch einen Serverneustart überleben. Die Alternativen:

| Policy | Verhalten | Für dieses System |
|---|---|---|
| `write_through` | jeder Zugriff wird sofort weitergeschrieben | ✅ gewünscht |
| `write_through_selective` | nur „heiße" Einträge werden weitergeschrieben | ✗ ein selten abgefragter Ast fiele heraus |
| `write_back` | Weiterschreiben erst bei Verdrängung | ✗ ein Crash verliert den Cache |

*(Default: `write_through`)*

**`--hicache-storage-backend file`**
Das L3-Backend. `file` ist der einfachste Weg für eine einzelne Instanz: KV-Pages
landen als Dateien auf der lokalen Platte. Weitere Optionen, die SGLang
akzeptiert: `mooncake` (verteilter Store mit RDMA, für Multi-Node),
`hf3fs` (DeepSeeks 3FS), `nixl` (NVIDIA-Transferlayer, wählt den konkreten Store
selbst), `aibrix`, `eic`, `simm`, `mori`, `shm`, sowie `dynamic` für ein selbst
mitgebrachtes Backend via `--hicache-storage-backend-extra-config`.
Alle sind im UI-Setting auswählbar. *(Default: keins — L3 ist ohne dieses Flag aus.)*

**`--hicache-storage-prefetch-policy wait_complete`**
Steuert, wann das Prefetching aus L3 abbricht:

| Policy | Verhalten |
|---|---|
| `best_effort` | bricht ab, sobald das Scheduling weiterlaufen will |
| `wait_complete` | wartet, bis der komplette Präfix geladen ist |
| `timeout` | bricht nach einer Zeitschranke ab |

Für dieses System ist `wait_complete` sinnvoll: ein Teiltreffer auf einem
200k-Token-Präfix zerstört den Nutzen — der nicht geladene Rest muss ohnehin neu
geprefillt werden, und dann kann man auch gleich warten.
**Achtung: SGLangs Default ist `timeout`, nicht `best_effort`** — das Flag muss
explizit gesetzt werden.

**`--hicache-io-backend direct`**
Wie KV-Daten zwischen CPU und GPU bewegt werden. `direct` nutzt direkte
Kopien, `kernel` einen dedizierten CUDA-Kernel (SGLangs Default), `kernel_ascend`
den Ascend-Pfad. SGLang korrigiert unpassende Kombinationen aus IO-Backend und
`--hicache-mem-layout` beim Start selbstständig und loggt das.

**`--page-size 64`**
Tokens pro Cache-Page. Gröbere Pages = weniger Verwaltungsaufwand, aber die
Trefferlänge wird auf ein Vielfaches davon abgerundet. Bei Präfixen dieser Größe
ist 64 unkritisch.

**`--mem-fraction-static 0.85`**
Anteil des VRAM für Gewichte plus KV-Pool. Höher = größerer L1-Cache, aber
weniger Luft für Aktivierungen. Bei OOM zuerst hier heruntergehen.

**`--reasoning-parser qwen3`**
Trennt die `<think>`-Inhalte ab und liefert sie als `reasoning_content` getrennt
vom Antworttext aus. Hermes parst das aus dem Stream und speichert Thinking und
Antwort getrennt.

**`--enable-metrics`**
Schaltet den Prometheus-Endpunkt `/metrics` frei. Ohne das Flag bleiben Cache-Baum
und Dashboard leer.

**`--api-key "$WEB_PASSWORD"`**
SGLang prüft den Wert als `Authorization: Bearer <key>`. Genau so schickt Hermes
ihn. Sollte deine vast.ai-Vorlage einen eigenen Reverse Proxy davorsetzen, der
einen anderen Header erwartet, sind Header-Name und Schema in den Hermes-Settings
(`sglang_auth_header`, `sglang_auth_scheme`) frei konfigurierbar.

---

## 3. Checkpoint verifizieren — wichtig

`nvidia/Qwen3.6-27B-NVFP4` ist auf Hugging Face mit `pipeline_tag:
text-generation` markiert, obwohl das Basismodell `Qwen/Qwen3.6-27B`
`image-text-to-text` ist. Bei ModelOpt-Quantisierungen wird üblicherweise nur der
Sprachteil quantisiert und der Vision-Tower unverändert mitgeliefert — garantiert
ist das aber nicht. **Vor dem ersten Ingest prüfen**, sonst rendert man erst
tausende Seiten und stellt dann fest, dass das Modell keine Bilder annimmt:

```bash
# 1. Nimmt der Server überhaupt Bilder an?
curl -s http://localhost:30000/v1/chat/completions \
  -H "Authorization: Bearer $WEB_PASSWORD" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "nvidia/Qwen3.6-27B-NVFP4",
    "max_tokens": 16,
    "messages": [{"role": "user", "content": [
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="}},
      {"type": "text", "text": "Welche Farbe hat dieses Bild?"}
    ]}]
  }'
```

Kommt hier ein Fehler wie *"multimodal input not supported"*, dann fehlt der
Vision-Tower. Rückfalloption: das unquantisierte Basismodell fahren und die
Quantisierung weglassen —

```bash
--model-path Qwen/Qwen3.6-27B      # statt nvidia/…-NVFP4
# --quantization modelopt_fp4      # entfällt
```

— dafür braucht es allerdings deutlich mehr VRAM.

Zusätzlich prüfen:

```bash
curl -s http://localhost:30000/get_model_info -H "Authorization: Bearer $WEB_PASSWORD"
curl -s http://localhost:30000/get_server_info -H "Authorization: Bearer $WEB_PASSWORD" | python3 -m json.tool | grep -i hicache
```

Die zweite Zeile muss die HiCache-Felder zeigen. Tut sie das nicht, läuft die
Instanz ohne `--enable-hierarchical-cache` und der ganze Ansatz trägt nicht.

Und die Vision-Token-Geometrie gegenprüfen, weil Hermes' Tokenschätzung darauf
aufbaut:

```bash
python3 -c "
import json, urllib.request
url='https://huggingface.co/Qwen/Qwen3.6-27B/raw/main/preprocessor_config.json'
cfg=json.load(urllib.request.urlopen(url))
print('patch_size', cfg['patch_size'], 'merge_size', cfg['merge_size'])
print('px pro Vision-Token-Kante:', cfg['patch_size']*cfg['merge_size'])
"
```

Erwartet: `patch_size 16 merge_size 2` → **32 px**. Weicht das ab, die Werte in
Hermes unter *Einstellungen → Vision-Token-Modell* nachziehen.

---

## 4. Cloudflare Tunnel

Schneller Weg (temporäre URL, gut zum Testen):

```bash
cloudflared tunnel --url http://localhost:30000
```

Die ausgegebene `https://….trycloudflare.com`-URL als `SGLANG_BASE_URL` in die
`.env` von Hermes eintragen. Diese URLs sind flüchtig und wechseln bei jedem
Neustart von `cloudflared`.

Dauerhaft (benannter Tunnel, eigene Domain):

```bash
cloudflared tunnel login
cloudflared tunnel create hermes-sglang
cloudflared tunnel route dns hermes-sglang sglang.deine-domain.de
cloudflared tunnel run --url http://localhost:30000 hermes-sglang
```

Der Tunnel schützt nicht selbst — die Absicherung ist `--api-key`. Ohne gesetztes
`WEB_PASSWORD` wäre der Endpunkt öffentlich erreichbar.

## 5. Verbindung aus Hermes prüfen

```bash
curl -fsS "$SGLANG_BASE_URL/health" -H "Authorization: Bearer $SGLANG_WEB_PASSWORD"
curl -fsS "$SGLANG_BASE_URL/metrics" -H "Authorization: Bearer $SGLANG_WEB_PASSWORD" | grep -E 'hicache|cache_hit'
```

In der UI zeigt der Indikator oben rechts den Status samt Latenz. Bei 401/403
stimmt das Passwort oder der Header-Name nicht; bei 404 zeigt die Base-URL nicht
auf SGLang.

## 6. Betrieb

**Warmlauf nach einem Neustart.** Mit `write_through` und
`--hicache-storage-backend file` überleben die Präfixe in L3. Nach einem
Serverneustart sind sie nicht sofort in L1 — die erste Anfrage je Sammlung holt
sie aus L3 zurück, was deutlich schneller ist als ein vollständiges Prefill, aber
nicht kostenlos. Die Sammlungen einmal durchzuingestieren ist der sauberere Weg.

**Cache leeren.** `POST /flush_cache?timeout=30` verwirft alles. Die UI bietet
das mit Sicherheitsabfrage an und setzt danach alle Sammlungen auf `stale`.

**Wenn die Trefferquote einbricht**, in dieser Reihenfolge prüfen:

1. Zeigt Hermes die Sammlung als `stale`? Dann hat sich der Präfix geändert
   (DPI, Dokumentreihenfolge, System-Prompt) — neu ingestieren.
2. Läuft die Instanz noch mit denselben Flags? Die Settings-Seite meldet
   Abweichungen über `/get_server_info`.
3. Ist der Host-Pool voll? `sglang:hicache_host_used_tokens` gegen
   `sglang:hicache_host_total_tokens` im Dashboard vergleichen. Wenn ja:
   `--hicache-ratio` erhöhen oder weniger Sammlungen gleichzeitig warmhalten.
4. Läuft `parallel_collection_queries`? Parallele Requests verdrängen sich
   gegenseitig aus L1. Standardmäßig ist die Option aus — sie sollte es bleiben.
