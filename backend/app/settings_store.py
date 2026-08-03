"""Typed key/value settings, seeded from ``.env`` and editable from the UI.

Every server-side knob lives here so the settings panel can render it with a
group, a type, a description, and — where relevant — the note that it is a
*launch* parameter of the external vast.ai instance rather than something this
process can change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.orm import Session

from . import prompts
from .config import env_config
from .models import Setting
from .tokens import (
    DEFAULT_MAX_PIXELS,
    DEFAULT_MERGE_SIZE,
    DEFAULT_MIN_PIXELS,
    DEFAULT_PATCH_SIZE,
)

SettingType = Literal["str", "int", "float", "bool", "text", "select"]


@dataclass(frozen=True)
class SettingSpec:
    key: str
    group: str
    label: str
    type: SettingType
    default: Any
    description: str = ""
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    #: Launch-time parameter of the external SGLang process; shown read-only-ish
    #: and fed into the generated launch command instead of being applied here.
    launch_param: bool = False
    #: Changing this invalidates every warmed collection.
    invalidates_cache: bool = False
    secret: bool = False
    advanced: bool = False
    tags: tuple[str, ...] = field(default=())


def _specs() -> list[SettingSpec]:
    env = env_config()
    return [
        # ---------------- connection ----------------
        SettingSpec("sglang_base_url", "connection", "SGLang Base-URL", "str",
                    env.sglang_base_url,
                    "URL des Cloudflare-Tunnels zur vast.ai-Instanz, ohne abschließenden Slash."),
        SettingSpec("sglang_web_password", "connection", "WEB_PASSWORD", "str",
                    env.sglang_web_password,
                    "Wird als Auth-Header mitgeschickt. Entspricht --api-key beim Serverstart.",
                    secret=True),
        SettingSpec("sglang_auth_header", "connection", "Auth-Header-Name", "str",
                    env.sglang_auth_header,
                    "Standard ist 'Authorization'. Manche vast.ai-Vorlagen erwarten stattdessen "
                    "'X-API-Key' oder 'X-Web-Password' — dann hier umstellen.",
                    advanced=True),
        SettingSpec("sglang_auth_scheme", "connection", "Auth-Schema", "str",
                    env.sglang_auth_scheme,
                    "Präfix vor dem Passwort, z. B. 'Bearer'. Leer lassen, wenn der Header das "
                    "nackte Passwort erwartet (üblich bei 'X-API-Key').",
                    advanced=True),
        SettingSpec("sglang_connect_timeout", "connection", "Connect-Timeout (s)", "float",
                    env.sglang_connect_timeout, "Timeout für den Verbindungsaufbau.",
                    minimum=1, maximum=120),
        SettingSpec("sglang_read_timeout", "connection", "Read-Timeout (s)", "float",
                    env.sglang_read_timeout,
                    "Großzügig setzen: das erste Prefill über 200k Tokens dauert Minuten.",
                    minimum=30, maximum=7200),
        SettingSpec("sglang_max_retries", "connection", "Retries", "int",
                    env.sglang_max_retries, "Wiederholungen bei Netzwerk-/5xx-Fehlern.",
                    minimum=0, maximum=10),
        SettingSpec("sglang_retry_backoff", "connection", "Retry-Backoff (s)", "float",
                    env.sglang_retry_backoff, "Basiswert; wächst exponentiell je Versuch.",
                    minimum=0.1, maximum=60),

        # ---------------- model / sampling ----------------
        SettingSpec("sglang_model", "model", "Modellname", "str", env.sglang_model,
                    "Wird als 'model' im Request gesendet und mit /get_model_info abgeglichen."),
        SettingSpec("max_context_tokens", "model", "Kontextlänge", "int",
                    env.max_context_tokens,
                    "Nativer Kontext des Modells. Muss zu --context-length der Instanz passen.",
                    minimum=4096, maximum=2097152),
        SettingSpec("collection_max_tokens", "model", "max_tokens (Sammlungsantwort)", "int",
                    1536, "Antwortbudget je Einzelantwort. Bewusst klein — die Antworten sollen kurz sein.",
                    minimum=32, maximum=32768),
        SettingSpec("synthesis_max_tokens", "model", "max_tokens (Synthese)", "int",
                    3072, "Antwortbudget der zusammengeführten Antwort.",
                    minimum=32, maximum=32768),
        SettingSpec("temperature", "model", "Temperature", "float", 0.2,
                    "0 macht Antworten reproduzierbar; leicht darüber wirkt natürlicher.",
                    minimum=0, maximum=2),
        SettingSpec("top_p", "model", "Top-p", "float", 0.9, "Nucleus-Sampling.",
                    minimum=0, maximum=1),
        SettingSpec("top_k", "model", "Top-k", "int", -1, "-1 deaktiviert Top-k.",
                    minimum=-1, maximum=1000),
        SettingSpec("repetition_penalty", "model", "Repetition Penalty", "float", 1.0,
                    "1.0 = aus.", minimum=0.5, maximum=2.0),
        SettingSpec("stop_sequences", "model", "Stop-Sequenzen", "text", "",
                    "Eine Sequenz pro Zeile. Leer = keine."),
        SettingSpec("thinking_collection", "model", "Thinking bei Sammlungsanfragen", "bool",
                    True, "Sendet chat_template_kwargs.enable_thinking. Reasoning kommt dank "
                          "--reasoning-parser qwen3 getrennt als reasoning_content zurück."),
        SettingSpec("thinking_synthesis", "model", "Thinking bei der Synthese", "bool", True,
                    "Wie oben, für den Zusammenführungsschritt."),

        # ---------------- vision token model ----------------
        SettingSpec("vision_patch_size", "vision", "ViT patch_size", "int", DEFAULT_PATCH_SIZE,
                    "Aus der preprocessor_config.json des Modells. Qwen3.6-27B: 16 "
                    "(Qwen2.5-VL war 14).", minimum=2, maximum=64, advanced=True),
        SettingSpec("vision_merge_size", "vision", "merge_size", "int", DEFAULT_MERGE_SIZE,
                    "Räumliches Merging. patch_size × merge_size ergibt die Pixel-Kantenlänge "
                    "pro Vision-Token — bei Qwen3.6-27B also 32×32 px.",
                    minimum=1, maximum=8, advanced=True),
        SettingSpec("vision_min_pixels", "vision", "min_pixels", "int", DEFAULT_MIN_PIXELS,
                    "Untere Flächenklammer des Processors; kleinere Bilder werden hochskaliert.",
                    advanced=True),
        SettingSpec("vision_max_pixels", "vision", "max_pixels", "int", DEFAULT_MAX_PIXELS,
                    "Obere Flächenklammer; größere Bilder werden heruntergerechnet.",
                    advanced=True),

        # ---------------- hicache (launch params) ----------------
        SettingSpec("hicache_ratio", "hicache", "hicache-ratio", "float", 2.0,
                    "Verhältnis Host-Pool zu Device-Pool.", minimum=1, maximum=32,
                    launch_param=True),
        SettingSpec("hicache_size", "hicache", "hicache-size (GB)", "int", 0,
                    "Host-Pool in GB. Überschreibt hicache-ratio, wenn > 0.",
                    minimum=0, maximum=4096, launch_param=True),
        SettingSpec("hicache_write_policy", "hicache", "hicache-write-policy", "select",
                    "write_through",
                    "write_through schreibt jeden Zugriff sofort in die nächste Stufe durch — "
                    "genau das ist hier gewollt, damit Bild-Präfixe dauerhaft in L3 überleben.",
                    choices=("write_through", "write_through_selective", "write_back"),
                    launch_param=True),
        SettingSpec("hicache_storage_backend", "hicache", "hicache-storage-backend", "select",
                    "file", "L3-Backend. 'file' ist der einfachste Weg für eine Einzelinstanz.",
                    choices=("file", "mooncake", "hf3fs", "nixl", "aibrix", "dynamic",
                             "eic", "simm", "mori", "shm"),
                    launch_param=True),
        SettingSpec("hicache_storage_prefetch_policy", "hicache",
                    "hicache-storage-prefetch-policy", "select", "wait_complete",
                    "Wann das Prefetching aus L3 abbricht. wait_complete, weil ein Teiltreffer "
                    "auf einem 200k-Token-Präfix den Nutzen zerstört. (SGLang-Default: timeout)",
                    choices=("best_effort", "wait_complete", "timeout"), launch_param=True),
        SettingSpec("hicache_io_backend", "hicache", "hicache-io-backend", "select", "direct",
                    "KV-Transfer zwischen CPU und GPU.",
                    choices=("direct", "kernel", "kernel_ascend"), launch_param=True),
        SettingSpec("hicache_mem_layout", "hicache", "hicache-mem-layout", "select",
                    "page_first", "Layout des Host-Pools. SGLang korrigiert unpassende "
                    "Kombinationen mit dem IO-Backend beim Start selbst.",
                    choices=("layer_first", "page_first", "page_first_direct",
                             "page_first_kv_split", "page_head"),
                    launch_param=True, advanced=True),
        SettingSpec("page_size", "hicache", "page-size", "int", 64,
                    "Tokens je Cache-Page. Grobere Pages = weniger Overhead, gröbere Treffer.",
                    minimum=1, maximum=256, launch_param=True),
        SettingSpec("mem_fraction_static", "hicache", "mem-fraction-static", "float", 0.85,
                    "Anteil des VRAM für Gewichte + KV-Pool.", minimum=0.1, maximum=0.98,
                    launch_param=True),
        SettingSpec("quantization", "hicache", "quantization", "str", "modelopt_fp4",
                    "Passend zum NVFP4-Checkpoint.", launch_param=True, advanced=True),
        SettingSpec("reasoning_parser", "hicache", "reasoning-parser", "str", "qwen3",
                    "Trennt <think>-Inhalte in reasoning_content ab.",
                    launch_param=True, advanced=True),
        SettingSpec("sglang_port", "hicache", "port", "int", 30000,
                    "Port, auf dem SGLang in der Instanz lauscht.", minimum=1, maximum=65535,
                    launch_param=True, advanced=True),

        # ---------------- document conversion ----------------
        SettingSpec("default_pdf_dpi", "conversion", "Standard-DPI", "int",
                    env.default_pdf_dpi,
                    "Globaler Default. Pro Sammlung und pro Upload überschreibbar.",
                    minimum=72, maximum=300),
        SettingSpec("default_max_edge", "conversion", "Max. Kantenlänge (px)", "int",
                    env.default_max_edge,
                    "Downscaling nach dem Rendern. 0 = kein Limit.", minimum=0, maximum=8192),
        SettingSpec("default_image_format", "conversion", "Bildformat", "select",
                    env.default_image_format,
                    "PNG ist verlustfrei, JPEG spart Platz und Upload-Zeit.",
                    choices=("png", "jpeg")),
        SettingSpec("default_jpeg_quality", "conversion", "JPEG-Qualität", "int",
                    env.default_jpeg_quality, "Nur bei Format JPEG.", minimum=30, maximum=100),
        SettingSpec("default_grayscale", "conversion", "Graustufen", "bool",
                    env.default_grayscale,
                    "Spart Dateigröße. Ändert die Tokenzahl nicht — die hängt allein an den "
                    "Pixelmaßen."),

        # ---------------- query ----------------
        SettingSpec("parallel_collection_queries", "query", "Sammlungen parallel abfragen",
                    "bool", False,
                    "Aus lassen. Parallele Requests verdrängen sich gegenseitig aus dem "
                    "GPU-Cache (L1) und erzwingen teure Rückholungen aus L2/L3."),
        SettingSpec("reserved_output_tokens", "query", "Reservierte Ausgabe-Tokens", "int",
                    env.reserved_output_tokens,
                    "Wird vom Kontextfenster abgezogen, bevor das Budget einer Sammlung "
                    "berechnet wird.", minimum=256, maximum=65536),
        SettingSpec("synthesis_enabled", "query", "Synthese aktiv", "bool", True,
                    "Führt die Einzelantworten zusammen."),
        SettingSpec("skip_synthesis_single_collection", "query",
                    "Synthese bei nur einer Sammlung überspringen", "bool", True,
                    "Spart einen kompletten Roundtrip, wenn es nichts zu synthetisieren gibt."),
        SettingSpec("history_turns", "query", "Berücksichtigte Vorgänger-Nachrichten", "int", 6,
                    "Chat-Verlauf wird ausschließlich hinter dem Bildblock bzw. in der Synthese "
                    "verwendet — niemals davor.", minimum=0, maximum=50),
        SettingSpec("no_hit_marker", "query", "Kein-Treffer-Marker", "str", "KEIN TREFFER",
                    "Genau diese Ausgabe erwartet der Sammlungs-Prompt bei einem Nichtfund. "
                    "Solche Antworten werden aus der Synthese herausgefiltert."),

        # ---------------- prompts ----------------
        SettingSpec("global_system_prompt", "prompts", "Globaler System-Prompt", "text",
                    prompts.GLOBAL_SYSTEM_PROMPT,
                    "Wurzel des Radix-Baums, identisch für alle Sammlungen. Jede Änderung "
                    "invalidiert sämtliche Caches.", invalidates_cache=True),
        SettingSpec("page_label_template", "prompts", "Seitenlabel-Template", "str",
                    prompts.PAGE_LABEL_TEMPLATE,
                    "Steht direkt vor jedem Seitenbild und ermöglicht Quellenangaben. "
                    "Platzhalter: {filename} {page_number} {document_pages} {sequence_index}. "
                    "Teil des Präfixes — jede Änderung invalidiert sämtliche Caches.",
                    invalidates_cache=True),
        SettingSpec("collection_instruction", "prompts", "Sammlungs-Prompt", "text",
                    prompts.COLLECTION_INSTRUCTION,
                    "Steht nach dem Bildblock. Platzhalter: {collection_name}. Ändert den "
                    "Präfix nur hinter den Bildern — die Bilder bleiben gecacht."),
        SettingSpec("synthesis_prompt", "prompts", "Synthese-Prompt", "text",
                    prompts.SYNTHESIS_PROMPT,
                    "Führt die Einzelantworten zusammen, ohne die Mechanik offenzulegen."),
    ]


SPECS: dict[str, SettingSpec] = {s.key: s for s in _specs()}


def _coerce(spec: SettingSpec, raw: str) -> Any:
    if spec.type == "int":
        return int(float(raw))
    if spec.type == "float":
        return float(raw)
    if spec.type == "bool":
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return raw


def _serialize(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def seed_defaults(db: Session) -> None:
    existing = {row.key for row in db.query(Setting.key).all()}
    for spec in SPECS.values():
        if spec.key not in existing:
            db.add(Setting(key=spec.key, value=_serialize(spec.default)))
    db.commit()


def all_settings(db: Session) -> dict[str, Any]:
    stored = {row.key: row.value for row in db.query(Setting).all()}
    out: dict[str, Any] = {}
    for key, spec in SPECS.items():
        raw = stored.get(key)
        if raw is None:
            out[key] = spec.default
            continue
        try:
            out[key] = _coerce(spec, raw)
        except (TypeError, ValueError):
            out[key] = spec.default
    return out


def get(db: Session, key: str) -> Any:
    return all_settings(db)[key]


def update(db: Session, values: dict[str, Any]) -> list[str]:
    """Persist settings. Returns the keys that invalidate warmed caches."""
    invalidating: list[str] = []
    for key, value in values.items():
        spec = SPECS.get(key)
        if spec is None:
            raise KeyError(f"Unbekanntes Setting: {key}")
        serialized = _serialize(value)
        row = db.get(Setting, key)
        previous = row.value if row else _serialize(spec.default)
        if row is None:
            db.add(Setting(key=key, value=serialized))
        else:
            row.value = serialized
        if spec.invalidates_cache and previous != serialized:
            invalidating.append(key)
    db.commit()
    return invalidating


def reset(db: Session, keys: list[str]) -> list[str]:
    return update(db, {k: SPECS[k].default for k in keys if k in SPECS})


def describe() -> list[dict[str, Any]]:
    return [
        {
            "key": s.key,
            "group": s.group,
            "label": s.label,
            "type": s.type,
            "default": s.default,
            "description": s.description,
            "choices": list(s.choices),
            "minimum": s.minimum,
            "maximum": s.maximum,
            "launch_param": s.launch_param,
            "invalidates_cache": s.invalidates_cache,
            "secret": s.secret,
            "advanced": s.advanced,
        }
        for s in SPECS.values()
    ]


def stop_sequences(values: dict[str, Any]) -> list[str]:
    raw = str(values.get("stop_sequences") or "")
    return [line for line in (l.strip() for l in raw.splitlines()) if line]


def build_launch_command(values: dict[str, Any]) -> str:
    """Render the vast.ai launch command that matches the current settings."""
    lines = [
        "python3 -m sglang.launch_server \\",
        f"  --model-path {values['sglang_model']} \\",
    ]
    if values.get("quantization"):
        lines.append(f"  --quantization {values['quantization']} \\")
    if values.get("reasoning_parser"):
        lines.append(f"  --reasoning-parser {values['reasoning_parser']} \\")
    lines += [
        f"  --context-length {values['max_context_tokens']} \\",
        f"  --host 0.0.0.0 --port {values['sglang_port']} \\",
        '  --api-key "$WEB_PASSWORD" \\',
        f"  --page-size {values['page_size']} \\",
        f"  --mem-fraction-static {values['mem_fraction_static']} \\",
        "  --enable-hierarchical-cache \\",
    ]
    if int(values.get("hicache_size") or 0) > 0:
        lines.append(f"  --hicache-size {values['hicache_size']} \\")
    else:
        lines.append(f"  --hicache-ratio {values['hicache_ratio']} \\")
    lines += [
        f"  --hicache-write-policy {values['hicache_write_policy']} \\",
        f"  --hicache-storage-backend {values['hicache_storage_backend']} \\",
        f"  --hicache-storage-prefetch-policy {values['hicache_storage_prefetch_policy']} \\",
        f"  --hicache-io-backend {values['hicache_io_backend']} \\",
        f"  --hicache-mem-layout {values['hicache_mem_layout']} \\",
        "  --enable-metrics",
    ]
    return "\n".join(lines)


#: Keys whose value we can compare against a running instance's /get_server_info.
SERVER_INFO_COMPARISON: dict[str, str] = {
    "hicache_ratio": "hicache_ratio",
    "hicache_size": "hicache_size",
    "hicache_write_policy": "hicache_write_policy",
    "hicache_storage_backend": "hicache_storage_backend",
    "hicache_storage_prefetch_policy": "hicache_storage_prefetch_policy",
    "hicache_io_backend": "hicache_io_backend",
    "hicache_mem_layout": "hicache_mem_layout",
    "page_size": "page_size",
    "mem_fraction_static": "mem_fraction_static",
    "max_context_tokens": "context_length",
    "reasoning_parser": "reasoning_parser",
    "quantization": "quantization",
}


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
