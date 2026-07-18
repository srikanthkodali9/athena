"""Degraded fallback: bounded retries then explicit degraded mode (FR4-05/06)."""

from __future__ import annotations

import httpx

from app.athena.ports import ChatMessage, ChatRequest, MockInference
from app.models.nim_client import NimChatClient, NimClientConfig
from app.routing.router import TieredInferenceRouter
from tests.inference.conftest import make_nim_client, static_responder


def _always_500():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"data: error\n\n")

    return handler


def _counting_500():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, content=b"")

    return handler, calls


async def test_retries_are_bounded():
    handler, calls = _counting_500()
    client = NimChatClient(
        NimClientConfig(
            base_url="https://mock.invalid/v1",
            model_id="m",
            tier="main",
            max_retries=2,
            backoff_seconds=0.0,
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    council = make_nim_client(static_responder("{}"), tier="council")
    router = TieredInferenceRouter(main=client, council=council)
    result = await router.complete(
        ChatRequest(messages=[ChatMessage(role="user", content="hi")], model_role="strategic")
    )
    assert calls["n"] == 3  # 1 + max_retries(2), bounded
    assert result.degraded is True


async def test_degraded_no_fallback_returns_empty_json():
    main = make_nim_client(_always_500(), tier="main", max_retries=0)
    council = make_nim_client(static_responder("{}"), tier="council")
    router = TieredInferenceRouter(main=main, council=council)
    result = await router.complete(
        ChatRequest(messages=[ChatMessage(role="user", content="hi")], model_role="strategic")
    )
    assert result.degraded is True
    assert result.content == "{}"
    assert result.model_id == "unavailable"
    assert "degraded" in router.last_metrics.routing_reason


async def test_degraded_uses_deterministic_fallback_when_wired():
    main = make_nim_client(_always_500(), tier="main", max_retries=0)
    council = make_nim_client(static_responder("{}"), tier="council")
    router = TieredInferenceRouter(
        main=main, council=council, degraded_fallback=MockInference("mock/nemotron")
    )
    result = await router.complete(
        ChatRequest(messages=[ChatMessage(role="user", content="hi")], model_role="strategic")
    )
    assert result.degraded is True
    assert result.model_id == "mock/nemotron"
    # MockInference returns canned strategic JSON.
    assert "hypothesis" in result.content
    assert router.last_metrics.tier == "fallback"
