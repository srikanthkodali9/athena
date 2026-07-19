"""Guarded LIVE smoke test — proves reasoning handling against BOTH Nemotron
models on real NVIDIA NIM. Skipped unless ATHENA_MAIN_MODEL_API_KEY is set.

Run: uv run pytest tests/inference/test_live_smoke.py -q -s
"""

from __future__ import annotations

import os

import pytest

from app.athena.config import get_settings
from app.athena.ports import ChatMessage, ChatRequest
from app.athena.schemas import Evidence
from app.council.specialists import ObservabilitySpecialist, PlatformSpecialist
from app.models.json_extract import extract_json
from app.routing.router import TieredInferenceRouter

pytestmark = pytest.mark.skipif(
    not os.getenv("ATHENA_MAIN_MODEL_API_KEY"),
    reason="live NIM keys not configured (ATHENA_MAIN_MODEL_API_KEY unset)",
)


async def _run_role(router: TieredInferenceRouter, role: str, question: str) -> None:
    req = ChatRequest(
        messages=[
            ChatMessage(
                role="user",
                content=(
                    f"{question} Reply with ONLY compact JSON of the form "
                    '{"answer": "<one short string>", "confidence": <0..1 number>}.'
                ),
            )
        ],
        model_role=role,
        max_tokens=512,
        response_schema={"required": ["answer", "confidence"]},
    )
    result = await router.complete(req)
    parsed = extract_json(result.content)
    m = router.last_metrics
    print(
        f"\n[LIVE {role} -> {m.tier}] model={result.model_id} "
        f"ttft_ms={m.ttft_ms} latency_ms={result.latency_ms} "
        f"tokens={m.prompt_tokens}+{m.completion_tokens} degraded={result.degraded} "
        f"repaired={m.repaired}\n  content={result.content}"
    )
    assert parsed is not None, f"{role}: no parseable JSON in {result.content!r}"
    assert "answer" in parsed and "confidence" in parsed
    assert result.degraded is False


async def test_live_both_models_return_parseable_answer():
    settings = get_settings()
    if settings.inference_mode.value != "live" or not settings.main_model_base_url:
        pytest.skip("inference_mode is not live or main model not configured")
    router = TieredInferenceRouter.from_env(settings)
    # strategic -> Nemotron-Super (main); specialist -> Nemotron-Nano (council)
    await _run_role(router, "strategic", "Name the capital of France.")
    await _run_role(router, "specialist", "Is 17 a prime number?")


def _redis_evidence() -> tuple[list[Evidence], list[Evidence]]:
    obs_ev = [
        Evidence(
            id="ev_metric-api-19",
            type="metric",
            source="prometheus",
            data={"series": "checkout_api_5xx_ratio", "baseline": 0.001, "current": 0.042,
                  "onset": "14:02Z"},
        ),
        Evidence(
            id="ev_metric-redis-42",
            type="metric",
            source="prometheus",
            data={"series": "redis_rejected_connections", "baseline": 0, "current": 318,
                  "onset": "14:01Z"},
        ),
    ]
    plat_ev = [
        Evidence(
            id="ev_diff-deploy-7",
            type="diff",
            source="kubernetes",
            data={"object": "deploy/checkout-api", "field": "REDIS_MAX_CONNECTIONS",
                  "before": 100, "after": 10, "deployed_at": "14:00Z"},
        ),
    ]
    return obs_ev, plat_ev


async def test_live_both_specialists_return_populated_findings():
    """Acceptance for Issue 1: BOTH Observability and Platform specialists come
    back non-degraded and evidence-CITING on the real Nemotron-Nano endpoint."""
    settings = get_settings()
    if settings.inference_mode.value != "live" or not settings.council_model_base_url:
        pytest.skip("inference_mode is not live or council model not configured")
    router = TieredInferenceRouter.from_env(settings)
    obs_ev, plat_ev = _redis_evidence()

    obs = ObservabilitySpecialist(router)
    plat = PlatformSpecialist(router)

    # Repeat a couple of times to show reliability, not a lucky single pass.
    for i in range(2):
        o = await obs.run(
            "Did Redis connection rejection precede the checkout API 5xx spike?", obs_ev
        )
        p = await plat.run(
            "Did a deployment change the Redis connection pool size, causing rejections?",
            plat_ev,
        )
        print(
            f"\n[LIVE COUNCIL run {i}] "
            f"\n  observability: degraded={o.degraded} confidence={o.finding.confidence} "
            f"cited={o.finding.cited_evidence_ids} rejected={o.rejected_citations} "
            f"model={o.model_id} prompt={o.prompt_version}"
            f"\n    hypothesis={o.finding.hypothesis!r}"
            f"\n  platform:      degraded={p.degraded} confidence={p.finding.confidence} "
            f"cited={p.finding.cited_evidence_ids} rejected={p.rejected_citations} "
            f"model={p.model_id} prompt={p.prompt_version}"
            f"\n    hypothesis={p.finding.hypothesis!r}"
        )
        assert o.degraded is False, f"observability degraded on run {i}"
        assert o.finding.cited_evidence_ids, f"observability cited nothing on run {i}"
        assert p.degraded is False, f"platform degraded on run {i}"
        assert p.finding.cited_evidence_ids, f"platform cited nothing on run {i}"
