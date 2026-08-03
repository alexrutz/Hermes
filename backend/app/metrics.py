"""Prometheus exposition-format parser for SGLang's ``/metrics``.

Two things this deliberately handles rather than assumes:

* **Prefix drift.** SGLang exported ``sglang:cache_hit_rate`` historically and
  switched to ``sglang_cache_hit_rate`` around v0.5.4. Both are normalised to
  the canonical dotted-free name so callers never care which server they hit.
* **Missing metrics.** HiCache gauges only exist when the server actually runs
  with ``--enable-hierarchical-cache``. A missing metric returns ``None``, not
  zero — "we don't know" and "it is empty" are different statements and the UI
  shows them differently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_SAMPLE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?"
    r"\s+(?P<value>[^\s]+)"
)
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


@dataclass
class Sample:
    name: str
    labels: dict[str, str]
    value: float


@dataclass
class MetricsSnapshot:
    samples: list[Sample] = field(default_factory=list)
    raw_names: set[str] = field(default_factory=set)

    def value(self, name: str) -> float | None:
        for sample in self.samples:
            if sample.name == name:
                return sample.value
        return None

    def sum(self, name: str) -> float | None:
        values = [s.value for s in self.samples if s.name == name]
        return sum(values) if values else None

    def histogram_average(self, name: str) -> float | None:
        total = self.sum(f"{name}_sum")
        count = self.sum(f"{name}_count")
        if total is None or not count:
            return None
        return total / count


def _normalize(name: str) -> str:
    """``sglang:foo`` and ``sglang_foo`` both become ``sglang_foo``."""
    if name.startswith("sglang:"):
        return "sglang_" + name[len("sglang:") :]
    return name


def parse_metrics(text: str) -> MetricsSnapshot:
    snapshot = MetricsSnapshot()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE.match(line)
        if not match:
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        raw_name = match.group("name")
        labels = dict(_LABEL.findall(match.group("labels") or ""))
        snapshot.raw_names.add(raw_name)
        snapshot.samples.append(Sample(_normalize(raw_name), labels, value))
    return snapshot


def summarize(text: str) -> dict[str, Any]:
    """Reduce the exposition text to the handful of numbers the UI shows."""
    snap = parse_metrics(text)

    host_used = snap.value("sglang_hicache_host_used_tokens")
    host_total = snap.value("sglang_hicache_host_total_tokens")
    hicache_available = host_total is not None

    used = snap.value("sglang_num_used_tokens")
    max_total = snap.value("sglang_max_total_num_tokens")

    return {
        "hicache_enabled": hicache_available,
        "hicache_host_used_tokens": host_used,
        "hicache_host_total_tokens": host_total,
        "hicache_host_utilization": (
            host_used / host_total if host_used is not None and host_total else None
        ),
        "cache_hit_rate": snap.value("sglang_cache_hit_rate"),
        "prompt_tokens_total": snap.sum("sglang_prompt_tokens_total"),
        "generation_tokens_total": snap.sum("sglang_generation_tokens_total"),
        "cached_tokens_total": snap.sum("sglang_cached_tokens_total"),
        "num_running_reqs": snap.value("sglang_num_running_reqs"),
        "num_queue_reqs": snap.value("sglang_num_queue_reqs"),
        "num_used_tokens": used,
        "max_total_num_tokens": max_total,
        "token_usage": snap.value("sglang_token_usage")
        or (used / max_total if used is not None and max_total else None),
        "gen_throughput": snap.value("sglang_gen_throughput"),
        "ttft_seconds_avg": snap.histogram_average("sglang_time_to_first_token_seconds"),
        "e2e_latency_seconds_avg": snap.histogram_average(
            "sglang_e2e_request_latency_seconds"
        ),
        "metric_count": len(snap.samples),
    }


def delta_hit_rate(before: dict[str, Any], after: dict[str, Any]) -> float | None:
    """Server-side hit rate for just the window between two snapshots.

    ``sglang_cache_hit_rate`` is cumulative since server start, so it barely
    moves once a server has been up for a while. Differencing the two counters
    gives the rate that actually applies to the request we just made.
    """
    p0, p1 = before.get("prompt_tokens_total"), after.get("prompt_tokens_total")
    c0, c1 = before.get("cached_tokens_total"), after.get("cached_tokens_total")
    if None in (p0, p1, c0, c1):
        return None
    prompt_delta = p1 - p0
    if prompt_delta <= 0:
        return None
    return max(0.0, min(1.0, (c1 - c0) / prompt_delta))
