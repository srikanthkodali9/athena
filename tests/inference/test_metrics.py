"""Per-call metrics capture incl. reasoning handling + ttft (FR4-04, FR12-06)."""

from __future__ import annotations

from app.athena.ports import ChatMessage, ChatRequest
from app.routing.router import TieredInferenceRouter
from tests.inference.conftest import make_nim_client, static_responder


def _req(role="strategic", schema=None):
    return ChatRequest(
        messages=[ChatMessage(role="user", content="hi")],
        model_role=role,
        response_schema=schema,
        max_tokens=300,
    )


async def test_captures_tokens_ttft_latency():
    usage = {"prompt_tokens": 41, "completion_tokens": 18, "total_tokens": 59}
    handler = static_responder(
        '{"ok": true}', reasoning="thinking about it", usage=usage
    )
    main = make_nim_client(handler, model_id="nemotron-super", tier="main")
    council = make_nim_client(handler, model_id="nemotron-nano", tier="council")
    router = TieredInferenceRouter(main=main, council=council)

    result = await router.complete(_req())
    m = router.last_metrics
    assert m.prompt_tokens == 41
    assert m.completion_tokens == 18
    assert m.total_tokens == 59
    assert m.ttft_ms is not None and m.ttft_ms >= 0
    assert m.latency_ms >= 0
    assert m.reasoning_tokens_estimate > 0  # reasoning trace was seen
    # Side-channel fields until core adds them to ChatResult.
    assert getattr(result, "ttft_ms", None) is not None
    assert getattr(result, "routing_reason", None)


async def test_reasoning_content_kept_out_of_answer():
    # Answer channel is clean JSON; the reasoning preamble lives in reasoning_content.
    handler = static_responder(
        '{"decision": "CONTINUE_PLAN", "reason": "ok"}',
        reasoning="We must decide. Evidence supports continuing.",
    )
    main = make_nim_client(handler, model_id="nemotron-super", tier="main")
    council = make_nim_client(handler, model_id="nemotron-nano", tier="council")
    router = TieredInferenceRouter(main=main, council=council)
    result = await router.complete(_req(schema={"required": ["decision"]}))
    assert "We must decide" not in result.content
    assert '"decision"' in result.content


async def test_empty_answer_all_reasoning_is_degraded():
    # All budget spent on reasoning: content empty -> degraded flag set.
    handler = static_responder(
        "", reasoning="endless deliberation with no answer", finish_reason="length"
    )
    main = make_nim_client(handler, model_id="nemotron-super", tier="main")
    council = make_nim_client(handler, model_id="nemotron-nano", tier="council")
    router = TieredInferenceRouter(main=main, council=council)
    result = await router.complete(_req())
    assert result.degraded is True
