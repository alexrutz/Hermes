"""In-process progress registry for conversion and ingest jobs.

Single-user, single-process system, so an in-memory registry with an asyncio
event per job is enough — no broker, no polling loop. Jobs are keyed by
collection id because only one long-running operation per collection may run at
a time; that invariant is enforced by :meth:`JobRegistry.start`.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Job:
    collection_id: str
    kind: str  # "convert" | "ingest"
    phase: str = "queued"
    current: int = 0
    total: int = 0
    message: str = ""
    error: str = ""
    finished: bool = False
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "collection_id": self.collection_id,
            "kind": self.kind,
            "phase": self.phase,
            "current": self.current,
            "total": self.total,
            "percent": round(100 * self.current / self.total, 1) if self.total else 0.0,
            "message": self.message,
            "error": self.error,
            "finished": self.finished,
            "elapsed_s": round(time.time() - self.started_at, 1),
            "result": self.result,
        }


class JobConflict(RuntimeError):
    pass


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._events: dict[str, asyncio.Event] = {}

    def start(self, collection_id: str, kind: str, total: int = 0) -> Job:
        running = self._jobs.get(collection_id)
        if running and not running.finished:
            raise JobConflict(
                f"Für diese Sammlung läuft bereits ein Vorgang ({running.kind}: {running.phase})."
            )
        job = Job(collection_id=collection_id, kind=kind, total=total)
        self._jobs[collection_id] = job
        self._events[collection_id] = asyncio.Event()
        return job

    def update(self, collection_id: str, **fields: Any) -> None:
        job = self._jobs.get(collection_id)
        if not job:
            return
        for key, value in fields.items():
            setattr(job, key, value)
        job.updated_at = time.time()
        if event := self._events.get(collection_id):
            event.set()

    def finish(self, collection_id: str, **fields: Any) -> None:
        self.update(collection_id, finished=True, phase=fields.pop("phase", "done"), **fields)

    def get(self, collection_id: str) -> Job | None:
        return self._jobs.get(collection_id)

    def active(self) -> list[dict[str, Any]]:
        return [j.snapshot() for j in self._jobs.values() if not j.finished]

    async def wait(self, collection_id: str, timeout: float = 1.0) -> None:
        event = self._events.get(collection_id)
        if not event:
            await asyncio.sleep(timeout)
            return
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        else:
            event.clear()


registry = JobRegistry()
