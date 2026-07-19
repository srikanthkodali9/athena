"""OpenAI-compatible NVIDIA NIM chat client (httpx, streaming).

One :class:`NimChatClient` serves exactly one endpoint + model + key
(strategic Nemotron-Super **or** council Nemotron-Nano). It is deliberately thin
and vendor-shaped so no NIM/vLLM detail leaks into orchestration or Council
contracts — the router and specialists depend only on this adapter.

Why streaming:
    We stream (`stream=True` + `stream_options.include_usage`) so we can record a
    real **time-to-first-token** (FR12-06) and still read exact token usage from
    the terminal chunk. NIM streams the private chain-of-thought as
    ``reasoning_content`` deltas and the answer as ``content`` deltas; we keep
    them apart so the answer stays clean for JSON parsing.

Reasoning control (Nemotron):
    Nemotron reasoning models honour a system-message toggle. For structured
    operational calls we prepend ``detailed thinking off`` (configurable via
    ``reasoning_mode``) to spend the token budget on the answer, not the trace.
    Verified against the live model card: the toggle reduces — but does not always
    eliminate — ``reasoning_content``, so degraded/repair handling still applies.

Robustness:
    * per-call timeout (connect + read) from the request,
    * bounded retries on transient transport/5xx/429 errors,
    * an explicit ``degraded`` flag when the answer channel comes back empty
      (all budget spent on reasoning, or a truncating ``finish_reason``).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.athena.ports import ChatMessage
from app.models.errors import InferenceUnavailable

# Nemotron system toggles. "detailed thinking off" is the documented control that
# suppresses most reasoning; "detailed thinking on" opts back in for hard calls.
REASONING_OFF = "detailed thinking off"
REASONING_ON = "detailed thinking on"

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass
class NimClientConfig:
    base_url: str
    model_id: str
    api_key: str = ""
    tier: str = "main"  # "main" | "council" | "fallback" — label only
    max_retries: int = 2  # bounded (AGENTS.md); total attempts = max_retries + 1
    backoff_seconds: float = 0.4
    connect_timeout: float = 10.0
    # A reasoning model needs headroom for the trace *plus* the answer even with
    # "detailed thinking off"; never let a caller's tiny max_tokens starve it.
    min_max_tokens: int = 256

    def masked(self) -> dict:
        host = self.base_url.split("//")[-1].split("/")[0] if self.base_url else ""
        return {
            "tier": self.tier,
            "model_id": self.model_id,
            "host": host,
            "has_key": bool(self.api_key),
            "max_retries": self.max_retries,
        }


@dataclass
class NimResult:
    """Raw adapter result before schema handling (that is the router's job)."""

    content: str
    reasoning_content: str
    model_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ttft_ms: Optional[float] = None
    latency_ms: float = 0.0
    finish_reason: Optional[str] = None
    degraded: bool = False
    attempts: int = 1
    tier: str = "main"
    _usage: dict = field(default_factory=dict)


class NimChatClient:
    """Async OpenAI-compatible chat client over one NIM endpoint."""

    def __init__(
        self,
        config: NimClientConfig,
        *,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._config = config
        # An injected client is the test seam (httpx.MockTransport). When we own
        # it we build it lazily so construction never opens a socket.
        self._client = client
        self._owns_client = client is None

    @property
    def config(self) -> NimClientConfig:
        return self._config

    @property
    def model_id(self) -> str:
        return self._config.model_id

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def health(self) -> dict:
        return {
            "component": "nim_client",
            "healthy": bool(self._config.base_url and self._config.model_id),
            **self._config.masked(),
        }

    # -- request building --------------------------------------------------

    def _build_messages(
        self, messages: list[ChatMessage], reasoning_mode: str
    ) -> list[dict]:
        out: list[dict] = []
        toggle = REASONING_OFF if reasoning_mode == "off" else REASONING_ON
        # Fold the toggle into an existing leading system message, or prepend one.
        if messages and messages[0].role == "system":
            out.append(
                {"role": "system", "content": f"{toggle}\n\n{messages[0].content}"}
            )
            rest = messages[1:]
        else:
            out.append({"role": "system", "content": toggle})
            rest = messages
        out.extend({"role": m.role, "content": m.content} for m in rest)
        return out

    # -- public API --------------------------------------------------------

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        timeout_seconds: float = 30.0,
        reasoning_mode: str = "off",
    ) -> NimResult:
        """Stream one chat completion; return content, trace, usage, timings.

        Raises :class:`InferenceUnavailable` only after retries are exhausted.
        """
        budget = max(max_tokens, self._config.min_max_tokens)
        payload = {
            "model": self._config.model_id,
            "messages": self._build_messages(messages, reasoning_mode),
            "max_tokens": budget,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        url = self._config.base_url.rstrip("/") + "/chat/completions"
        headers = {"Accept": "text/event-stream"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        timeout = httpx.Timeout(
            timeout_seconds, connect=self._config.connect_timeout
        )

        last_exc: Optional[Exception] = None
        attempts = 0
        for attempt in range(self._config.max_retries + 1):
            attempts = attempt + 1
            try:
                result = await self._stream_once(url, payload, headers, timeout)
                result.attempts = attempts
                return result
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
            except _RetryableStatus as exc:
                last_exc = exc
            except httpx.HTTPStatusError as exc:  # non-retryable 4xx
                raise InferenceUnavailable(
                    f"{self._config.tier} endpoint rejected request: "
                    f"HTTP {exc.response.status_code}"
                ) from exc
            if attempt < self._config.max_retries:
                await asyncio.sleep(self._config.backoff_seconds * (attempt + 1))

        raise InferenceUnavailable(
            f"{self._config.tier} endpoint unavailable after {attempts} attempt(s): "
            f"{type(last_exc).__name__}"
        ) from last_exc

    async def _stream_once(
        self, url: str, payload: dict, headers: dict, timeout: httpx.Timeout
    ) -> NimResult:
        client = self._get_client()
        started = time.perf_counter()
        ttft_ms: Optional[float] = None
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: dict = {}
        finish_reason: Optional[str] = None

        async with client.stream(
            "POST", url, json=payload, headers=headers, timeout=timeout
        ) as response:
            if response.status_code in _RETRYABLE_STATUS:
                await response.aread()
                raise _RetryableStatus(response.status_code)
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if obj.get("usage"):
                    usage = obj["usage"]
                for choice in obj.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                    piece = delta.get("content")
                    if piece:
                        if ttft_ms is None:
                            ttft_ms = round((time.perf_counter() - started) * 1000, 2)
                        content_parts.append(piece)
                    trace = delta.get("reasoning_content")
                    if trace:
                        if ttft_ms is None:
                            ttft_ms = round((time.perf_counter() - started) * 1000, 2)
                        reasoning_parts.append(trace)

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        content = "".join(content_parts).strip()
        reasoning = "".join(reasoning_parts)
        # Degraded: the answer channel is empty (budget spent on reasoning) or the
        # generation was truncated mid-answer.
        degraded = not content or finish_reason == "length"
        return NimResult(
            content=content,
            reasoning_content=reasoning,
            model_id=self._config.model_id,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            ttft_ms=ttft_ms,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            degraded=degraded,
            tier=self._config.tier,
            _usage=usage,
        )


class _RetryableStatus(Exception):
    """Internal signal that a retryable HTTP status was returned."""

    def __init__(self, status: int) -> None:
        super().__init__(f"retryable status {status}")
        self.status = status
