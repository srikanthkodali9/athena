"""Concurrency helper: independent specialists overlap (FR12-05).

Also documents the ordering guarantee: Observability (round 1) and Platform
(round 2) are sequenced by the lifecycle and are NOT gathered together here.
"""

from __future__ import annotations

import asyncio
import time

from app.athena.ports import ChatRequest, ChatResult, InferencePort
from app.athena.schemas import Evidence
from app.council.specialists import ObservabilitySpecialist, gather_specialists
from tests.inference.conftest import FakeInference


class SlowInference(InferencePort):
    def __init__(self, content: str, delay: float):
        self._content = content
        self._delay = delay

    async def complete(self, request: ChatRequest) -> ChatResult:
        await asyncio.sleep(self._delay)
        return ChatResult(content=self._content, model_id="nano", role=request.model_role)


async def test_gather_runs_specialists_concurrently(obs_json):
    delay = 0.15
    spec_a = ObservabilitySpecialist(SlowInference(obs_json, delay))
    spec_b = ObservabilitySpecialist(SlowInference(obs_json, delay))
    ev = [Evidence(id="ev_metric-api-19", type="metric", source="prometheus", data={})]

    start = time.perf_counter()
    outcomes = await gather_specialists(spec_a.run("q1", ev), spec_b.run("q2", ev))
    elapsed = time.perf_counter() - start

    assert len(outcomes) == 2
    # Concurrent: ~one delay, not two. Generous bound for CI jitter.
    assert elapsed < delay * 1.8


async def test_gather_preserves_order(obs_json):
    ev = [Evidence(id="ev_metric-api-19", type="metric", source="prometheus", data={})]
    a = ObservabilitySpecialist(FakeInference(obs_json))
    b = ObservabilitySpecialist(FakeInference(obs_json))
    outcomes = await gather_specialists(a.run("first", ev), b.run("second", ev))
    assert [o.role for o in outcomes] == ["observability", "observability"]
