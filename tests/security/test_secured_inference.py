"""Every actual model attempt crosses the centralized HiddenLayer boundary."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.athena.ports import (
    ChatMessage,
    ChatRequest,
    ChatResult,
    NoOpSecurityGateway,
)
from app.athena.schemas import Evidence
from app.council.schemas import ObservabilitySpecialistOutput
from app.council.specialists import ObservabilitySpecialist
from app.routing.router import TieredInferenceRouter
from app.security.contracts import (
    AgentEffect,
    FindingType,
    InspectionStage,
    SecurityAction,
    SecurityDecision,
    Severity,
)
from app.security.inference import (
    SecuredInferencePort,
    SecurityBoundaryDenied,
)


class SequenceInference:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResult:
        self.calls.append(request)
        content = self._responses.pop(0)
        return ChatResult(
            content=content,
            model_id="test/model",
            role=request.model_role,
            prompt_tokens=3,
            completion_tokens=2,
        )


class RecordingGateway(NoOpSecurityGateway):
    def __init__(self) -> None:
        super().__init__()
        self.requests = []

    def inspect(self, request):
        self.requests.append(request)
        return super().inspect(request)


def _redacted(content: str) -> SecurityDecision:
    return SecurityDecision(
        action=SecurityAction.REDACT,
        severity=Severity.MEDIUM,
        confidence=1.0,
        finding=FindingType.SECRET_EXPOSURE,
        agent_effect=AgentEffect.REDACTED,
        safe_content=content.replace("sensitive-marker", "[REDACTED]"),
    )


def _blocked() -> SecurityDecision:
    return SecurityDecision(
        action=SecurityAction.BLOCK,
        severity=Severity.CRITICAL,
        confidence=1.0,
        finding=FindingType.PROMPT_INJECTION,
        agent_effect=AgentEffect.BLOCKED,
        safe_content=None,
    )


def _evidence() -> list[Evidence]:
    return [
        Evidence(
            id="ev_metric-full-prompt",
            type="metric",
            source="prometheus",
            provenance={"fixture": "test"},
            data={"diagnostic_marker": "rendered-evidence-marker", "value": 99},
        )
    ]


def _valid_observability_json() -> str:
    payload = ObservabilitySpecialistOutput.example()
    payload["cited_evidence_ids"] = ["ev_metric-full-prompt"]
    return json.dumps(payload)


def test_specialist_inspects_fully_rendered_prompt_and_raw_response():
    gateway = RecordingGateway()
    inner = SequenceInference([_valid_observability_json()])
    specialist = ObservabilitySpecialist(SecuredInferencePort(inner, gateway))

    outcome = asyncio.run(
        specialist.run("Analyze the symptom.", _evidence(), cycle_id="cycle_test")
    )

    prompts = [
        request
        for request in gateway.requests
        if request.stage is InspectionStage.MODEL_PROMPT
    ]
    responses = [
        request
        for request in gateway.requests
        if request.stage is InspectionStage.SPECIALIST_RESPONSE
    ]
    assert len(prompts) == 1 and len(responses) == 1
    assert "rendered-evidence-marker" in prompts[0].content
    assert "Observability Specialist" in prompts[0].content
    assert responses[0].content == _valid_observability_json()
    assert outcome.security_inspections == 2


def test_specialist_repair_prompt_and_response_are_both_inspected():
    gateway = RecordingGateway()
    invalid = '{"hypothesis": 42, "sensitive": "repair-marker"}'
    inner = SequenceInference([invalid, _valid_observability_json()])
    specialist = ObservabilitySpecialist(SecuredInferencePort(inner, gateway))

    outcome = asyncio.run(
        specialist.run("Analyze the symptom.", _evidence(), cycle_id="cycle_test")
    )

    prompts = [r for r in gateway.requests if r.stage is InspectionStage.MODEL_PROMPT]
    responses = [
        r
        for r in gateway.requests
        if r.stage is InspectionStage.SPECIALIST_RESPONSE
    ]
    assert len(inner.calls) == 2
    assert len(prompts) == 2 and len(responses) == 2
    assert "repair-marker" in prompts[1].content
    assert outcome.inference_attempts == 2
    assert outcome.security_inspections == 4


def test_blocked_repair_prompt_never_reaches_model():
    class BlockSecondPrompt(RecordingGateway):
        def inspect(self, request):
            self.requests.append(request)
            prompt_count = sum(
                item.stage is InspectionStage.MODEL_PROMPT for item in self.requests
            )
            if request.stage is InspectionStage.MODEL_PROMPT and prompt_count == 2:
                return _blocked()
            return NoOpSecurityGateway.inspect(self, request)

    gateway = BlockSecondPrompt()
    inner = SequenceInference(["not json", _valid_observability_json()])
    specialist = ObservabilitySpecialist(SecuredInferencePort(inner, gateway))

    with pytest.raises(SecurityBoundaryDenied) as caught:
        asyncio.run(
            specialist.run(
                "Analyze the symptom.", _evidence(), cycle_id="cycle_test"
            )
        )

    assert len(inner.calls) == 1
    assert caught.value.inference_attempts == 1


def test_redacted_prompt_and_response_are_the_only_content_used_downstream():
    class RedactingGateway(RecordingGateway):
        def inspect(self, request):
            self.requests.append(request)
            return _redacted(request.content)

    gateway = RedactingGateway()
    inner = SequenceInference(["answer contains sensitive-marker"])
    secured = SecuredInferencePort(inner, gateway)
    request = ChatRequest(
        messages=[ChatMessage(role="user", content="inspect sensitive-marker")],
        model_role="strategic",
        cycle_id="cycle_test",
    )

    result = asyncio.run(secured.complete(request))

    assert "sensitive-marker" not in inner.calls[0].messages[0].content
    assert inner.calls[0].messages[0].content == "inspect [REDACTED]"
    assert result.content == "answer contains [REDACTED]"


def test_secured_port_owns_router_schema_repair_without_hidden_attempts():
    gateway = RecordingGateway()
    model = SequenceInference(
        ["not-json", '{"decision": "CONTINUE_PLAN", "reason": "supported"}']
    )
    router = TieredInferenceRouter(main=model, council=model)
    secured = SecuredInferencePort(router, gateway)
    request = ChatRequest(
        messages=[ChatMessage(role="user", content="Return a decision.")],
        model_role="strategic",
        response_schema={"required": ["decision", "reason"]},
        cycle_id="cycle_test",
    )

    result = asyncio.run(secured.complete(request))

    prompts = [r for r in gateway.requests if r.stage is InspectionStage.MODEL_PROMPT]
    responses = [
        r for r in gateway.requests if r.stage is InspectionStage.ATHENA_RESPONSE
    ]
    assert len(model.calls) == 2
    assert all(call.security_managed_retries for call in model.calls)
    assert len(prompts) == 2 and len(responses) == 2
    assert "not-json" in prompts[1].content
    assert json.loads(result.content)["decision"] == "CONTINUE_PLAN"
    assert result.inference_attempts == 2
    assert result.security_inspections == 4
