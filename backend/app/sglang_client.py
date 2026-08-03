"""Everything that talks to the external SGLang instance lives here.

Kept in one file on purpose: SGLang's API moves fast, so when a flag name, an
endpoint or a response shape changes, there is exactly one place to fix it.

Verified against sglang ``main`` (see ARCHITECTURE.md §0):
  * auth  — ``--api-key K`` is checked as ``Authorization: Bearer K``; the
            header name and scheme are configurable because some vast.ai
            templates put their own proxy in front with a different header.
  * flush — ``/flush_cache`` accepts GET and POST with ``?timeout=<float>``.
  * chat  — ``/v1/chat/completions`` accepts ``separate_reasoning`` (default
            true) and ``chat_template_kwargs``; reasoning arrives in
            ``delta.reasoning_content``.
  * usage — ``usage.prompt_tokens_details.cached_tokens`` carries the hit count.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from . import metrics as metrics_mod

logger = logging.getLogger(__name__)


class SGLangError(RuntimeError):
    pass


@dataclass
class ClientConfig:
    base_url: str
    password: str = ""
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    connect_timeout: float = 15.0
    read_timeout: float = 1800.0
    max_retries: int = 3
    retry_backoff: float = 2.0

    def headers(self) -> dict[str, str]:
        head = {"Content-Type": "application/json"}
        if self.password:
            scheme = self.auth_scheme.strip()
            head[self.auth_header] = f"{scheme} {self.password}" if scheme else self.password
        return head

    @property
    def url(self) -> str:
        return self.base_url.rstrip("/")


@dataclass
class StreamChunk:
    kind: str  # "reasoning" | "content" | "usage" | "done"
    text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompletionResult:
    content: str = ""
    reasoning: str = ""
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    ttft_ms: int = 0

    @property
    def cache_hit_rate(self) -> float:
        return self.cached_tokens / self.prompt_tokens if self.prompt_tokens else 0.0


class SGLangClient:
    def __init__(self, config: ClientConfig):
        self.config = config
        self._timeout = httpx.Timeout(
            connect=config.connect_timeout,
            read=config.read_timeout,
            write=config.read_timeout,
            pool=config.connect_timeout,
        )

    # ------------------------------------------------------------------ utils
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.config.url, headers=self.config.headers(), timeout=self._timeout
        )

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                async with self._client() as client:
                    response = await client.get(path, params=params)
                if response.status_code >= 500:
                    raise SGLangError(f"{path} → HTTP {response.status_code}")
                return response
            except (httpx.HTTPError, SGLangError) as exc:
                last = exc
                if attempt < self.config.max_retries:
                    await asyncio.sleep(self.config.retry_backoff * (2**attempt))
        raise SGLangError(f"{path} nicht erreichbar: {last}") from last

    # ----------------------------------------------------------------- health
    async def health(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            response = await self._get("/health")
            latency = int((time.perf_counter() - started) * 1000)
            ok = response.status_code < 400
            return {
                "ok": ok,
                "status_code": response.status_code,
                "latency_ms": latency,
                "base_url": self.config.url,
                "error": "" if ok else _auth_hint(response.status_code),
            }
        except SGLangError as exc:
            return {
                "ok": False,
                "status_code": 0,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "base_url": self.config.url,
                "error": str(exc),
            }

    async def server_info(self) -> dict[str, Any]:
        response = await self._get("/get_server_info")
        if response.status_code >= 400:
            raise SGLangError(
                f"/get_server_info → HTTP {response.status_code} {_auth_hint(response.status_code)}"
            )
        return response.json()

    async def model_info(self) -> dict[str, Any]:
        response = await self._get("/get_model_info")
        if response.status_code >= 400:
            raise SGLangError(f"/get_model_info → HTTP {response.status_code}")
        return response.json()

    async def metrics_text(self) -> str:
        response = await self._get("/metrics")
        if response.status_code >= 400:
            raise SGLangError(
                f"/metrics → HTTP {response.status_code}. Läuft die Instanz mit --enable-metrics?"
            )
        return response.text

    async def metrics(self) -> dict[str, Any]:
        return metrics_mod.summarize(await self.metrics_text())

    async def metrics_safe(self) -> dict[str, Any] | None:
        """Metrics for the sampling path, where a failure must not abort a query."""
        try:
            return await self.metrics()
        except Exception as exc:  # noqa: BLE001 - sampling is strictly best effort
            logger.warning("Metrics-Abruf fehlgeschlagen: %s", exc)
            return None

    async def flush_cache(self, timeout: float = 30.0) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.post("/flush_cache", params={"timeout": timeout})
        return {
            "ok": response.status_code < 400,
            "status_code": response.status_code,
            "message": response.text.strip(),
        }

    # ------------------------------------------------------------------- chat
    def _payload(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        top_k: int,
        repetition_penalty: float,
        stop: list[str],
        thinking: bool,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "stream": stream,
            # Ask the qwen3 reasoning parser to hand back reasoning separately
            # instead of leaving <think> tags inline in the content.
            "separate_reasoning": True,
            "chat_template_kwargs": {"enable_thinking": bool(thinking)},
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if top_k is not None and top_k > 0:
            payload["top_k"] = top_k
        if repetition_penalty and repetition_penalty != 1.0:
            payload["repetition_penalty"] = repetition_penalty
        if stop:
            payload["stop"] = stop
        return payload

    async def chat_stream(self, **kwargs: Any) -> AsyncIterator[StreamChunk]:
        payload = self._payload(stream=True, **kwargs)
        started = time.perf_counter()
        first_token_at: float | None = None

        async with self._client() as client:
            async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")[:600]
                    raise SGLangError(
                        f"/v1/chat/completions → HTTP {response.status_code} "
                        f"{_auth_hint(response.status_code)} {body}"
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    if usage := event.get("usage"):
                        yield StreamChunk("usage", usage=usage)

                    for choice in event.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if reasoning := delta.get("reasoning_content"):
                            if first_token_at is None:
                                first_token_at = time.perf_counter()
                            yield StreamChunk("reasoning", text=reasoning)
                        if content := delta.get("content"):
                            if first_token_at is None:
                                first_token_at = time.perf_counter()
                            yield StreamChunk("content", text=content)

        ttft = int(((first_token_at or time.perf_counter()) - started) * 1000)
        yield StreamChunk(
            "done",
            usage={
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "ttft_ms": ttft,
            },
        )

    async def chat(self, **kwargs: Any) -> CompletionResult:
        """Non-streaming completion — used by warm-up and the benchmark."""
        payload = self._payload(stream=False, **kwargs)
        started = time.perf_counter()
        async with self._client() as client:
            response = await client.post("/v1/chat/completions", json=payload)
        if response.status_code >= 400:
            raise SGLangError(
                f"/v1/chat/completions → HTTP {response.status_code} "
                f"{_auth_hint(response.status_code)} {response.text[:600]}"
            )
        body = response.json()
        latency = int((time.perf_counter() - started) * 1000)
        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        result = CompletionResult(
            content=message.get("content") or "",
            reasoning=message.get("reasoning_content") or "",
            latency_ms=latency,
            ttft_ms=latency,
        )
        apply_usage(result, body.get("usage") or {})
        return result


def apply_usage(result: CompletionResult, usage: dict[str, Any]) -> None:
    """Copy token counters out of an OpenAI-shaped usage object."""
    if not usage:
        return
    result.prompt_tokens = int(usage.get("prompt_tokens") or 0)
    result.completion_tokens = int(usage.get("completion_tokens") or 0)
    details = usage.get("prompt_tokens_details") or {}
    if isinstance(details, dict):
        result.cached_tokens = int(details.get("cached_tokens") or 0)
    elif isinstance(details, (int, float)):
        result.cached_tokens = int(details)


def _auth_hint(status_code: int) -> str:
    if status_code in (401, 403):
        return (
            "(Authentifizierung abgelehnt — prüfe WEB_PASSWORD sowie Header-Name und "
            "Schema in den Settings; manche vast.ai-Vorlagen erwarten 'X-API-Key' "
            "statt 'Authorization: Bearer'.)"
        )
    if status_code == 404:
        return "(Endpunkt nicht gefunden — zeigt die Base-URL wirklich auf SGLang?)"
    return ""


def client_from_settings(values: dict[str, Any]) -> SGLangClient:
    return SGLangClient(
        ClientConfig(
            base_url=str(values["sglang_base_url"]),
            password=str(values["sglang_web_password"]),
            auth_header=str(values["sglang_auth_header"]) or "Authorization",
            auth_scheme=str(values["sglang_auth_scheme"]),
            connect_timeout=float(values["sglang_connect_timeout"]),
            read_timeout=float(values["sglang_read_timeout"]),
            max_retries=int(values["sglang_max_retries"]),
            retry_backoff=float(values["sglang_retry_backoff"]),
        )
    )


def sampling_kwargs(values: dict[str, Any], *, step: str) -> dict[str, Any]:
    """Sampling parameters for the ``collection`` or ``synthesis`` step."""
    from .settings_store import stop_sequences

    return {
        "model": str(values["sglang_model"]),
        "max_tokens": int(
            values["collection_max_tokens" if step == "collection" else "synthesis_max_tokens"]
        ),
        "temperature": float(values["temperature"]),
        "top_p": float(values["top_p"]),
        "top_k": int(values["top_k"]),
        "repetition_penalty": float(values["repetition_penalty"]),
        "stop": stop_sequences(values),
        "thinking": bool(
            values["thinking_collection" if step == "collection" else "thinking_synthesis"]
        ),
    }
