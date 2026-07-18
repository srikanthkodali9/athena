"""Structured-output validation + exactly-one repair retry (FR5-08)."""

from __future__ import annotations

import httpx

from app.athena.ports import ChatMessage, ChatRequest
from app.routing.router import TieredInferenceRouter
from tests.inference.conftest import DEFAULT_USAGE, make_nim_client, sse_stream

SCHEMA = {"required": ["decision", "reason"]}


def _stateful_handler(responses: list[str]):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        idx = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return httpx.Response(
            200,
            content=sse_stream(responses[idx], usage=DEFAULT_USAGE),
            headers={"content-type": "text/event-stream"},
        )

    return handler, calls


def _router_for(handler):
    main = make_nim_client(handler, model_id="nemotron-super", tier="main")
    council = make_nim_client(handler, model_id="nemotron-nano", tier="council")
    return TieredInferenceRouter(main=main, council=council)


def _req():
    return ChatRequest(
        messages=[ChatMessage(role="user", content="decide")],
        model_role="midcycle",
        response_schema=SCHEMA,
        max_tokens=300,
    )


async def test_valid_json_passes_without_repair():
    handler, calls = _stateful_handler(['{"decision": "CONTINUE_PLAN", "reason": "ok"}'])
    router = _router_for(handler)
    result = await router.complete(_req())
    assert result.degraded is False
    assert router.last_metrics.repaired is False
    assert calls["n"] == 1


async def test_invalid_then_repaired_once():
    # First reply is not valid JSON for the schema; repaired reply is valid.
    handler, calls = _stateful_handler(
        ["not json at all", '{"decision": "REVISE_PLAN", "reason": "redirected"}']
    )
    router = _router_for(handler)
    result = await router.complete(_req())
    assert router.last_metrics.repaired is True
    assert calls["n"] == 2  # exactly one repair retry
    assert '"decision":"REVISE_PLAN"' in result.content.replace(" ", "")


async def test_still_invalid_after_repair_is_degraded():
    handler, calls = _stateful_handler(["garbage one", "garbage two", "garbage three"])
    router = _router_for(handler)
    result = await router.complete(_req())
    assert calls["n"] == 2  # original + ONE repair, no more
    assert result.degraded is True
    assert router.last_metrics.repaired is True


async def test_no_schema_skips_validation():
    handler, calls = _stateful_handler(["free-form answer, no json"])
    router = _router_for(handler)
    req = ChatRequest(
        messages=[ChatMessage(role="user", content="hi")], model_role="strategic"
    )
    result = await router.complete(req)
    assert calls["n"] == 1
    assert result.content == "free-form answer, no json"
