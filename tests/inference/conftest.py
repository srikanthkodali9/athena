"""Deterministic fake HTTP layer for NIM adapter tests.

We drive :class:`NimChatClient` through an ``httpx.MockTransport`` that emits
Server-Sent-Events exactly like NVIDIA NIM (content + reasoning_content deltas,
plus a terminal usage chunk). No sockets, no keys, fully deterministic.
"""

from __future__ import annotations

import json
from typing import Callable, Optional

import httpx
import pytest

from app.athena.ports import ChatRequest, ChatResult, InferencePort
from app.models.nim_client import NimChatClient, NimClientConfig


def sse_stream(
    content: str,
    *,
    reasoning: str = "",
    finish_reason: str = "stop",
    usage: Optional[dict] = None,
) -> bytes:
    """Build a NIM-shaped SSE body."""
    chunks: list[dict] = []
    if reasoning:
        chunks.append({"choices": [{"delta": {"reasoning_content": reasoning}}]})
    if content:
        chunks.append({"choices": [{"delta": {"content": content}}]})
    chunks.append({"choices": [{"delta": {}, "finish_reason": finish_reason}]})
    if usage is not None:
        chunks.append({"choices": [], "usage": usage})
    body = b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks)
    return body + b"data: [DONE]\n\n"


DEFAULT_USAGE = {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32}


def make_nim_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    model_id: str = "test/model",
    tier: str = "main",
    max_retries: int = 1,
) -> NimChatClient:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return NimChatClient(
        NimClientConfig(
            base_url="https://mock.invalid/v1",
            model_id=model_id,
            api_key="test-key",
            tier=tier,
            max_retries=max_retries,
            backoff_seconds=0.0,
        ),
        client=client,
    )


def static_responder(
    content: str, *, reasoning: str = "", finish_reason: str = "stop", usage: dict | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse_stream(
                content,
                reasoning=reasoning,
                finish_reason=finish_reason,
                usage=usage or DEFAULT_USAGE,
            ),
            headers={"content-type": "text/event-stream"},
        )

    return handler


class FakeInference(InferencePort):
    """A plain InferencePort returning preset content (no HTTP)."""

    def __init__(self, content: str, *, model_id: str = "fake/model", degraded: bool = False):
        self._content = content
        self._model_id = model_id
        self._degraded = degraded
        self.calls: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResult:
        self.calls.append(request)
        return ChatResult(
            content=self._content,
            model_id=self._model_id,
            role=request.model_role,
            prompt_tokens=10,
            completion_tokens=8,
            latency_ms=1.0,
            degraded=self._degraded,
        )


@pytest.fixture
def obs_json() -> str:
    return json.dumps(
        {
            "hypothesis": "Redis connection saturation preceded the API 5xx spike.",
            "observed_symptoms": ["api 5xx rose at 14:02"],
            "timeline": ["14:00 baseline", "14:02 5xx spike"],
            "baseline_comparison": "5xx 0.1% -> 4.2% vs baseline",
            "cited_evidence_ids": ["ev_metric-api-19"],
            "confidence": 0.62,
            "missing_evidence": ["redis connection pool metric"],
        }
    )
