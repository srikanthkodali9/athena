"""Typed ports the core spine depends on — not vendor SDKs.

Two ports:

1. ``InferencePort`` — an OpenAI-compatible chat-completion contract that
   returns content plus token/latency metrics. The live vLLM adapter is the
   inference_engineer's Wave 2 job; a deterministic ``MockInference`` is used
   for Wave 1 tests.

2. ``SecurityGateway`` — the trust-inspection contract. The canonical version
   lives at ``app/security/contracts.py`` (owned by security_expert). Until that
   module exists we define a minimal LOCAL protocol with the SAME shape here and
   ship a ``NoOpSecurityGateway`` for tests. This MUST be reconciled to
   ``app/security/contracts.py`` at integration.

Both mock implementations count their calls so tests can assert, for example,
that the no-change WAIT path makes ZERO model calls (FR-2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable

# Canonical security contracts live in app/security/contracts.py (owned by
# security_expert). Core depends on that single shared port; we re-export the
# names here so existing core imports (`from app.athena.ports import ...`)
# resolve to the canonical types after Wave-1 integration.
from app.security.contracts import (
    AgentEffect,
    FindingType,
    InspectionRequest,
    InspectionStage,
    SecurityAction,
    SecurityDecision,
    SecurityGateway,
    Severity,
)

__all__ = [
    "ChatMessage",
    "ChatRequest",
    "ChatResult",
    "InferencePort",
    "MockInference",
    "AgentEffect",
    "FindingType",
    "InspectionRequest",
    "InspectionStage",
    "SecurityAction",
    "SecurityDecision",
    "SecurityGateway",
    "Severity",
    "NoOpSecurityGateway",
]

# ---------------------------------------------------------------------------
# Inference port
# ---------------------------------------------------------------------------


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str


@dataclass
class ChatRequest:
    messages: list[ChatMessage]
    model_role: str = "strategic"  # strategic | specialist | critic
    max_tokens: int = 1024
    temperature: float = 0.0
    response_schema: Optional[dict[str, Any]] = None
    timeout_seconds: float = 30.0


@dataclass
class ChatResult:
    content: str
    model_id: str
    role: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    degraded: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@runtime_checkable
class InferencePort(Protocol):
    """OpenAI-compatible chat completion the core consumes."""

    async def complete(self, request: ChatRequest) -> ChatResult:
        ...


# ---------------------------------------------------------------------------
# Deterministic mock implementations (Wave 1)
# ---------------------------------------------------------------------------


class MockInference:
    """Deterministic inference for tests and offline runs.

    Returns canned, structurally valid content keyed by ``model_role`` and
    increments ``calls`` so the no-change path can be proven model-free.
    """

    def __init__(self, model_id: str = "mock/nemotron") -> None:
        self.model_id = model_id
        self.calls = 0
        self.seen_roles: list[str] = []

    async def complete(self, request: ChatRequest) -> ChatResult:
        self.calls += 1
        self.seen_roles.append(request.model_role)
        content = _MOCK_RESPONSES.get(request.model_role, "{}")
        # Deterministic token accounting proportional to prompt size.
        prompt_tokens = sum(len(m.content) for m in request.messages) // 4
        completion_tokens = max(8, len(content) // 4)
        return ChatResult(
            content=content,
            model_id=self.model_id,
            role=request.model_role,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=1.0,
            degraded=True,
        )


_MOCK_RESPONSES: dict[str, str] = {
    "strategic": (
        '{"hypothesis": "Redis connection saturation degrades API latency", '
        '"confidence": 0.6}'
    ),
    "specialist": (
        '{"hypothesis": "5xx rate spike correlates with deployment", '
        '"confidence": 0.55}'
    ),
    "midcycle": '{"decision": "CONTINUE_PLAN", "reason": "evidence supports hypothesis"}',
    "critic": '{"validated": true, "unsupported_claims": []}',
}


class NoOpSecurityGateway:
    """Allow-all gateway implementing the canonical ``SecurityGateway`` port.

    Used by isolated core unit tests that want no real detection. The running
    application wires the real ``HiddenLayerGateway`` (see
    ``app/api/dependencies.py``). ``inspect`` is synchronous to match the
    canonical contract; the orchestrator's ``_inspect`` helper is await-tolerant
    so either style integrates. ``calls``/``stages`` are kept for assertions.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.stages: list[InspectionStage] = []

    def inspect(self, request: InspectionRequest) -> SecurityDecision:
        self.calls += 1
        self.stages.append(request.stage)
        return SecurityDecision(
            action=SecurityAction.ALLOW,
            severity=Severity.INFO,
            confidence=1.0,
            finding=FindingType.NONE,
            trust_score_before=1.0,
            trust_score_after=1.0,
            agent_effect=AgentEffect.USED_IN_CONTEXT,
            safe_content=request.content,
            rationale="noop-allow",
        )

    def readiness(self) -> dict:
        return {
            "component": "security_gateway",
            "mode": "mock",
            "adapter": "noop",
            "is_mock": True,
            "fail_closed": True,
            "healthy": True,
        }
