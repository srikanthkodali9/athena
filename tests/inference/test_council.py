"""Council specialist contracts: schema validation + evidence allowlisting."""

from __future__ import annotations

import json

from app.athena.ports import ChatRequest, ChatResult, InferencePort
from app.athena.schemas import Evidence
from app.council.specialists import ObservabilitySpecialist, PlatformSpecialist
from tests.inference.conftest import FakeInference


class SequenceInference(InferencePort):
    """Returns preset contents in order across successive calls."""

    def __init__(self, contents: list[str], *, model_id: str = "nemotron-nano"):
        self._contents = contents
        self._model_id = model_id
        self.calls: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResult:
        idx = min(len(self.calls), len(self._contents) - 1)
        self.calls.append(request)
        return ChatResult(
            content=self._contents[idx],
            model_id=self._model_id,
            role=request.model_role,
            completion_tokens=20,
        )


def _evidence():
    return [
        Evidence(id="ev_metric-api-19", type="metric", source="prometheus", data={"5xx": 4.2}),
        Evidence(id="ev_metric-redis-42", type="metric", source="prometheus", data={"conns": 999}),
    ]


async def test_observability_valid_output_becomes_finding(obs_json):
    spec = ObservabilitySpecialist(FakeInference(obs_json, model_id="nemotron-nano"))
    outcome = await spec.run("Did Redis saturation precede the API failure?", _evidence())
    assert outcome.role == "observability"
    assert outcome.finding.role == "observability"
    assert outcome.finding.confidence == 0.62
    assert outcome.finding.cited_evidence_ids == ["ev_metric-api-19"]
    assert outcome.degraded is False
    assert outcome.prompt_version == "obs-v2"


async def test_citation_outside_allowlist_is_rejected():
    payload = json.dumps(
        {
            "hypothesis": "x",
            "observed_symptoms": ["s"],
            "cited_evidence_ids": ["ev_metric-api-19", "ev_forbidden-99"],
            "confidence": 0.7,
            "missing_evidence": [],
        }
    )
    spec = ObservabilitySpecialist(FakeInference(payload))
    outcome = await spec.run("q", _evidence())
    assert "ev_forbidden-99" in outcome.rejected_citations
    assert outcome.finding.cited_evidence_ids == ["ev_metric-api-19"]
    assert outcome.degraded is True


async def test_all_citations_invalid_caps_confidence():
    payload = json.dumps(
        {
            "hypothesis": "unsupported claim",
            "cited_evidence_ids": ["ev_nope"],
            "confidence": 0.95,
            "missing_evidence": [],
        }
    )
    spec = ObservabilitySpecialist(FakeInference(payload))
    outcome = await spec.run("q", _evidence())
    assert outcome.finding.cited_evidence_ids == []
    assert outcome.finding.confidence <= 0.19


async def test_invalid_json_yields_degraded_empty_finding():
    spec = PlatformSpecialist(FakeInference("not json", degraded=False))
    outcome = await spec.run("What changed in the platform?", _evidence())
    assert outcome.degraded is True
    assert outcome.finding.role == "platform"
    assert outcome.finding.confidence == 0.0


async def test_extra_key_is_ignored_not_fatal():
    # Issue 1: a stray extra key must NOT zero the finding (extra="ignore").
    payload = json.dumps(
        {
            "hypothesis": "Redis saturation preceded the failure.",
            "observed_symptoms": ["5xx spike"],
            "cited_evidence_ids": ["ev_metric-api-19"],
            "confidence": 0.7,
            "missing_evidence": [],
            "unexpected_extra_key": "should be dropped, never persisted",
        }
    )
    fake = FakeInference(payload)
    spec = ObservabilitySpecialist(fake)
    outcome = await spec.run("q", _evidence())
    assert outcome.degraded is False
    assert outcome.finding.confidence == 0.7
    assert outcome.finding.cited_evidence_ids == ["ev_metric-api-19"]
    assert len(fake.calls) == 1  # no repair needed
    # The unknown key is dropped, not carried onto the finding/output.
    assert not hasattr(outcome.output, "unexpected_extra_key")


async def test_pydantic_type_error_triggers_one_repair(obs_json):
    # First reply validates as JSON but has a wrong-typed field (confidence as a
    # string) -> Pydantic ValidationError -> ONE targeted repair -> valid.
    bad = json.dumps(
        {
            "hypothesis": "x",
            "cited_evidence_ids": ["ev_metric-api-19"],
            "confidence": "very high",  # wrong type
            "missing_evidence": [],
        }
    )
    seq = SequenceInference([bad, obs_json])
    spec = ObservabilitySpecialist(seq)
    outcome = await spec.run("q", _evidence())
    assert len(seq.calls) == 2  # original + exactly one repair
    assert outcome.degraded is False
    assert outcome.finding.confidence == 0.62
    # The repair message carried the exact validation error + key list.
    repair_prompt = seq.calls[1].messages[-1].content
    assert "confidence" in repair_prompt
    assert "hypothesis" in repair_prompt


async def test_repair_exhausted_is_degraded_not_lost():
    seq = SequenceInference(["still not valid", "also invalid"])
    spec = ObservabilitySpecialist(seq)
    outcome = await spec.run("q", _evidence())
    assert len(seq.calls) == 2  # bounded to one repair
    assert outcome.degraded is True
    assert outcome.finding.role == "observability"


async def test_specialist_prompt_restricts_evidence(obs_json):
    fake = FakeInference(obs_json)
    spec = ObservabilitySpecialist(fake)
    await spec.run("q", _evidence())
    prompt = fake.calls[0].messages[0].content
    # Only allowlisted evidence IDs appear; the prompt enforces one narrow question.
    assert "ev_metric-api-19" in prompt
    assert "ev_metric-redis-42" in prompt
    assert "Do not recommend or perform final remediation." in prompt
    assert fake.calls[0].model_role == "specialist"
