"""Canonical security contracts — THE shared port for runtime security.

This module is the coordinator-agreed boundary imported by ``core_developer``
(``app/heartbeat``, ``app/athena``, ``app/tools``, ``app/memory``). Keep the
shapes below stable so cross-agent integration converges.

Two responsibilities are kept strictly independent (PRD §6.6 / §14):

* HiddenLayer (this gateway) answers "should this interaction be trusted?"
  -> :class:`SecurityGateway.inspect` returning a :class:`SecurityDecision`.
* OpenShell (``app.security.openshell``) answers "can this action cross the
  protected boundary?" -> a separate ``PolicyDecision``. Content trust never
  feeds capability enforcement and vice-versa.

Only ``pydantic``/stdlib are imported here so ``core`` can depend on this file
without pulling in adapters, httpx, or policy loading.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class InspectionStage(str, Enum):
    """Every information transition HiddenLayer inspects (PRD §14.2)."""

    HEARTBEAT_INPUT = "heartbeat_input"
    RETRIEVED_CONTENT = "retrieved_content"
    USER_PROMPT = "user_prompt"
    MODEL_PROMPT = "model_prompt"
    SPECIALIST_RESPONSE = "specialist_response"
    ATHENA_RESPONSE = "athena_response"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    MEMORY_WRITE = "memory_write"
    SKILL_WRITE = "skill_write"
    RUNBOOK_WRITE = "runbook_write"


class SecurityAction(str, Enum):
    """Coordinator-agreed action set applied by the gateway."""

    ALLOW = "allow"
    REDACT = "redact"
    EXCLUDE = "exclude"
    QUARANTINE = "quarantine"
    BLOCK = "block"
    REJECT_PERSISTENCE = "reject_persistence"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingType(str, Enum):
    NONE = "none"
    PROMPT_INJECTION = "prompt_injection"
    EXFILTRATION = "exfiltration"
    CREDENTIAL_ACCESS = "credential_access"
    SECRET_EXPOSURE = "secret_exposure"
    SUSPICIOUS_INSTRUCTION = "suspicious_instruction"
    POISONED_ARTIFACT = "poisoned_artifact"
    ANOMALY = "anomaly"
    INSPECTION_UNAVAILABLE = "inspection_unavailable"


class AgentEffect(str, Enum):
    """How the decision changes Athena's behaviour (PRD §14.3 agent_effect)."""

    USED_IN_CONTEXT = "used_in_context"
    USED_IN_RESTRICTED_MODE = "used_in_restricted_mode"
    REDACTED = "redacted"
    EXCLUDED_FROM_CONTEXT = "excluded_from_context"
    QUARANTINED = "quarantined"
    BLOCKED = "blocked"
    PERSISTENCE_REJECTED = "persistence_rejected"


# ---------------------------------------------------------------------------
# Stage classification — used by the gateway to pick fail-open vs fail-closed
# behaviour when inspection is unavailable. Exposed here so core and tests
# share one definition.
# ---------------------------------------------------------------------------

#: Actionable calls that can cross a real-world boundary.
ACTIONABLE_STAGES: frozenset[InspectionStage] = frozenset({InspectionStage.TOOL_CALL})

#: Writes that persist into memory / learning / proposals.
PERSISTENT_WRITE_STAGES: frozenset[InspectionStage] = frozenset(
    {
        InspectionStage.MEMORY_WRITE,
        InspectionStage.SKILL_WRITE,
        InspectionStage.RUNBOOK_WRITE,
    }
)

#: Stages that MUST fail closed when required inspection is unavailable
#: (PRD §14.4: tool call or persistent write -> fail closed).
FAIL_CLOSED_STAGES: frozenset[InspectionStage] = ACTIONABLE_STAGES | PERSISTENT_WRITE_STAGES


class InspectionRequest(BaseModel):
    """One trust transition submitted to the gateway."""

    stage: InspectionStage
    producer: str = Field(..., description="Source that produced the content.")
    consumer: str = Field(..., description="Component that would consume it.")
    content: str = Field("", description="Raw untrusted content under inspection.")
    cycle_id: str | None = Field(None, description="Owning heartbeat cycle.")
    metadata: dict = Field(default_factory=dict)

    model_config = {"frozen": True}


class SecurityDecision(BaseModel):
    """The gateway's verdict for one :class:`InspectionRequest`.

    The first block of fields is the coordinator-agreed shape core depends on.
    Trailing fields are additive (optional, defaulted) so core keeps
    converging even if it ignores them.
    """

    # --- coordinator-agreed core fields ---
    action: SecurityAction
    severity: Severity
    confidence: float = Field(..., ge=0.0, le=1.0)
    finding: FindingType = FindingType.NONE
    trust_score_before: float | None = None
    trust_score_after: float | None = None
    agent_effect: AgentEffect = AgentEffect.USED_IN_CONTEXT
    security_incident_created: bool = False

    # --- additive, optional ---
    #: Content safe to pass downstream. ``None`` means nothing may be used
    #: (excluded / quarantined / blocked / persistence rejected).
    safe_content: str | None = None
    #: Sanitized audit reference (``sec_XXXX``) if an event was recorded.
    security_event_id: str | None = None
    #: Short, sanitized explanation. Never contains raw payload or secrets.
    rationale: str | None = None

    def is_usable(self) -> bool:
        """True when the consumer may use ``safe_content`` downstream."""

        return self.action in (SecurityAction.ALLOW, SecurityAction.REDACT)


@runtime_checkable
class SecurityGateway(Protocol):
    """The single inspection port for all stages (FR-10-01).

    Implementations MUST be synchronous, side-effect-bounded, and MUST fail
    closed for :data:`FAIL_CLOSED_STAGES` when inspection is unavailable.
    """

    def inspect(self, request: InspectionRequest) -> SecurityDecision:
        ...
