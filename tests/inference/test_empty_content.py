"""Empty-answer retry: a reasoning model that spends all budget thinking must
not leave strategic/plan/synthesis calls with empty content (Issue 2)."""

from __future__ import annotations

import httpx

from app.athena.ports import ChatMessage, ChatRequest
from app.routing.router import TieredInferenceRouter
from tests.inference.conftest import DEFAULT_USAGE, make_nim_client, sse_stream


def _stateful(contents: list[str], *, reasoning: str = "long hidden reasoning"):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        idx = min(calls["n"], len(contents) - 1)
        calls["n"] += 1
        return httpx.Response(
            200,
            content=sse_stream(contents[idx], reasoning=reasoning, usage=DEFAULT_USAGE),
            headers={"content-type": "text/event-stream"},
        )

    return handler, calls


def _router(handler):
    main = make_nim_client(handler, model_id="nemotron-super", tier="main")
    council = make_nim_client(handler, model_id="nemotron-nano", tier="council")
    return TieredInferenceRouter(main=main, council=council)


def _req(schema=None):
    return ChatRequest(
        messages=[ChatMessage(role="user", content="plan the investigation")],
        model_role="strategic",
        response_schema=schema,
        max_tokens=512,
    )


async def test_empty_answer_retried_and_recovered():
    # First response: all reasoning, empty content. Retry: real answer.
    handler, calls = _stateful(["", '{"hypothesis": "redis", "confidence": 0.7}'])
    router = _router(handler)
    result = await router.complete(_req())
    assert calls["n"] == 2  # one empty-content retry
    assert result.content.strip() != ""
    assert "redis" in result.content


async def test_empty_answer_schemaless_never_returns_empty():
    # Plan/synthesis/critic go through raw _infer with NO schema; still must not
    # hand back an empty string.
    handler, calls = _stateful(["", ""])  # persistently empty
    router = _router(handler)
    result = await router.complete(_req(schema=None))
    assert result.content == "{}"  # degraded-but-non-empty fallback
    assert result.degraded is True


async def test_nonempty_answer_not_retried():
    handler, calls = _stateful(['{"hypothesis": "x", "confidence": 0.5}'])
    router = _router(handler)
    await router.complete(_req())
    assert calls["n"] == 1  # no unnecessary retry
