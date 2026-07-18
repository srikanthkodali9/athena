"""Routing-by-role: correct tier + recorded routing reason (FR4-01/02/04)."""

from __future__ import annotations

import pytest

from app.athena.ports import ChatMessage, ChatRequest
from app.routing.router import ROUTING_MATRIX, TieredInferenceRouter
from tests.inference.conftest import make_nim_client, static_responder


def _router():
    main = make_nim_client(
        static_responder('{"ok": true}'), model_id="nemotron-super", tier="main"
    )
    council = make_nim_client(
        static_responder('{"ok": true}'), model_id="nemotron-nano", tier="council"
    )
    return TieredInferenceRouter(main=main, council=council)


def _req(role: str) -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage(role="user", content="hi")], model_role=role, max_tokens=300
    )


@pytest.mark.parametrize(
    "role,expected_tier,expected_model",
    [
        ("strategic", "main", "nemotron-super"),
        ("midcycle", "main", "nemotron-super"),
        ("critic", "main", "nemotron-super"),
        ("specialist", "council", "nemotron-nano"),
    ],
)
async def test_routes_by_role(role, expected_tier, expected_model):
    router = _router()
    result = await router.complete(_req(role))
    assert result.model_id == expected_model
    assert router.last_metrics.tier == expected_tier
    assert router.last_metrics.routing_reason == ROUTING_MATRIX[role].reason
    assert result.role == role


async def test_unknown_role_fails_safe_to_main():
    router = _router()
    result = await router.complete(_req("mystery"))
    assert result.model_id == "nemotron-super"
    assert "fail safe" in router.last_metrics.routing_reason


async def test_metrics_logged_per_call():
    router = _router()
    await router.complete(_req("strategic"))
    await router.complete(_req("specialist"))
    assert len(router.metrics_log) == 2
    assert [m.tier for m in router.metrics_log] == ["main", "council"]
