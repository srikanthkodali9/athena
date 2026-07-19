"""Shared CORE domain schemas (FND-03 subset), Pydantic v2.

Design rule (AGENTS.md): deterministic facts are kept SEPARATE from model
interpretations and hypotheses.

- ``Evidence`` / ``EvidenceEnvelope`` / ``ToolResult`` carry deterministic facts
  and provenance produced by the tool registry.
- ``Finding`` carries a model interpretation and cites evidence IDs; it never
  becomes a fact on its own.
- ``InvestigationPlan`` is bounded (specialists, tool calls, tokens, deadline,
  stop conditions, one revision).
- ``Cycle`` / ``Checkpoint`` are the durable heartbeat records.

All schemas are versioned via ``schema_version`` so stored records remain
readable as the model evolves.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Decision(str, enum.Enum):
    """Heartbeat decision values (PRD 12.2)."""

    WAIT = "WAIT"
    INVESTIGATE = "INVESTIGATE"
    ESCALATE = "ESCALATE"
    CONTINUE = "CONTINUE"
    RETRY = "RETRY"
    LEARN = "LEARN"
    REQUEST_APPROVAL = "REQUEST_APPROVAL"
    CONTAIN = "CONTAIN"


class CycleState(str, enum.Enum):
    """Cycle state machine (FR1-03).

    Terminal states: COMPLETED, FAILED, CANCELLED.
    """

    QUEUED = "queued"
    LOADING = "loading"
    DETECTING = "detecting"
    PLANNING = "planning"
    INVESTIGATING = "investigating"
    SYNTHESIZING = "synthesizing"
    PERSISTING = "persisting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset(
    {CycleState.COMPLETED, CycleState.FAILED, CycleState.CANCELLED}
)


class TriggerKind(str, enum.Enum):
    manual = "manual"
    periodic = "periodic"


class ChangeClass(str, enum.Enum):
    """FR2-03 change classification."""

    irrelevant = "irrelevant"
    meaningful = "meaningful"
    security_sensitive = "security_sensitive"
    requires_revalidation = "requires_revalidation"


class MidCycleDecision(str, enum.Enum):
    CONTINUE_PLAN = "CONTINUE_PLAN"
    REVISE_PLAN = "REVISE_PLAN"


# ---------------------------------------------------------------------------
# Deterministic evidence plane (facts)
# ---------------------------------------------------------------------------


class Evidence(BaseModel):
    """One deterministic fact with provenance. Immutable once registered."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = SCHEMA_VERSION
    id: str = Field(description="Stable evidence id, e.g. 'ev_metric-api-19'.")
    type: str = Field(description="metric | log | manifest | diff | object | ...")
    source: str = Field(description="Provenance system, e.g. 'prometheus'.")
    provenance: dict[str, Any] = Field(
        default_factory=dict,
        description="How the fact was obtained (query, window, path, commit).",
    )
    data: dict[str, Any] = Field(
        default_factory=dict, description="The raw deterministic values."
    )
    trust: str = Field(
        default="unverified",
        description="Source trust label; lowered by the security gateway.",
    )
    collected_at: datetime = Field(default_factory=utcnow)


class ToolResult(BaseModel):
    """Standard deterministic tool-result envelope (PRD 17.3, FR6-02).

    ``evidence`` holds facts; ``summary`` is a deterministic human string, not a
    model interpretation.
    """

    schema_version: str = SCHEMA_VERSION
    tool: str
    status: str = Field(description="success | error")
    evidence: list[Evidence] = Field(default_factory=list)
    summary: str = ""
    error: Optional[str] = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    latency_ms: Optional[float] = None

    @property
    def evidence_ids(self) -> list[str]:
        return [e.id for e in self.evidence]


class EvidenceEnvelope(BaseModel):
    """Sanitized single-evidence view for the ``/evidence/{id}`` route (FR6)."""

    schema_version: str = SCHEMA_VERSION
    evidence: Evidence
    produced_by: str = Field(description="Tool or component that produced it.")
    cycle_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Model interpretation plane (hypotheses — never facts on their own)
# ---------------------------------------------------------------------------


class Finding(BaseModel):
    """A model interpretation citing evidence (FR6-03/04).

    A ``Finding`` is an interpretation/hypothesis. It must cite evidence IDs and
    is never promoted to fact without validation.
    """

    schema_version: str = SCHEMA_VERSION
    id: str
    role: str = Field(description="observability | platform | synthesis | critic")
    hypothesis: str
    interpretation: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    model_id: Optional[str] = None
    tokens: int = 0
    latency_ms: Optional[float] = None
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Bounded investigation plan (FR-3)
# ---------------------------------------------------------------------------


class RoundBudget(BaseModel):
    specialists: list[str] = Field(default_factory=list)
    max_tool_calls: int = Field(ge=0, default=5)
    max_tokens: int = Field(ge=0, default=4000)
    deadline_seconds: int = Field(ge=1, default=30)


class StopConditions(BaseModel):
    root_cause_confidence: float = Field(ge=0.0, le=1.0, default=0.85)
    containment_action_identified: bool = True
    no_new_evidence_after_queries: int = Field(ge=1, default=2)
    raw: list[str] = Field(
        default_factory=list, description="Original human-readable stop rules."
    )


class InvestigationPlan(BaseModel):
    """Bounded plan. No specialist/tool/model runs until this validates (FR3)."""

    schema_version: str = SCHEMA_VERSION
    plan_id: str
    incident_ref: Optional[str] = None
    max_specialists: int = Field(ge=1, le=2)
    max_tool_calls: int = Field(ge=1)
    max_tokens: int = Field(ge=1)
    deadline_seconds: int = Field(ge=1)
    first_round: RoundBudget
    second_round: RoundBudget
    stop_conditions: StopConditions = Field(default_factory=StopConditions)
    security_mode: str = "fail_closed_for_tools_and_writes"
    required_evidence: list[str] = Field(default_factory=list)
    # One-revision bound (FR3-10).
    revision_used: bool = False
    original_platform_question: Optional[str] = None
    platform_question: Optional[str] = None
    revision_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)

    def bounds_ok(self) -> bool:
        """Reject plans that omit mandatory bounds or exceed policy (FR3-06)."""
        return (
            self.max_specialists >= 1
            and self.max_tool_calls >= 1
            and self.max_tokens >= 1
            and self.deadline_seconds >= 1
            and (
                self.first_round.max_tokens + self.second_round.max_tokens
                <= self.max_tokens
            )
        )

    def revise_platform_question(self, new_question: str, reason: str) -> None:
        """Apply the single permitted mid-cycle revision (FR3-09/10)."""
        if self.revision_used:
            raise ValueError("Plan already revised once; revision bound is one.")
        self.original_platform_question = self.platform_question
        self.platform_question = new_question
        self.revision_reason = reason
        self.revision_used = True


# ---------------------------------------------------------------------------
# Durable heartbeat records
# ---------------------------------------------------------------------------


class Checkpoint(BaseModel):
    """A transactional record that a cycle step completed (FR7-04)."""

    schema_version: str = SCHEMA_VERSION
    cycle_id: str
    step: str
    status: str = "done"
    costly: bool = Field(
        default=False,
        description="Costly/irreversible steps must not repeat on recovery.",
    )
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class InferenceMetric(BaseModel):
    """Per-inference telemetry (FR4-04, FR12-06).

    Captured for every model call so the demo can show tiered routing, token
    usage, time-to-first-token, latency, and degraded mode.
    """

    schema_version: str = SCHEMA_VERSION
    model_id: Optional[str] = None
    role: str
    routing_reason: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    ttft_ms: Optional[float] = None
    latency_ms: Optional[float] = None
    degraded: bool = False


class CycleMetrics(BaseModel):
    model_calls: int = 0
    tool_calls: int = 0
    specialists_invoked: int = 0
    total_tokens: int = 0
    security_inspections: int = 0
    duration_ms: Optional[float] = None
    # One record per inference (model_id, role, routing_reason, tokens, ttft,
    # latency, degraded). Aggregates above stay for quick reads.
    inferences: list[InferenceMetric] = Field(default_factory=list)


class Cycle(BaseModel):
    """A persisted heartbeat cycle record (PRD 12.3, FR1-05, FR7-02)."""

    schema_version: str = SCHEMA_VERSION
    cycle_id: str
    trigger: TriggerKind
    reason: Optional[str] = None
    fixture: Optional[str] = None
    idempotency_key: Optional[str] = None

    state: CycleState = CycleState.QUEUED
    decision: Optional[Decision] = None
    state_before: Optional[CycleState] = None

    signals_checked: list[str] = Field(default_factory=list)
    changes_detected: list[dict[str, Any]] = Field(default_factory=list)
    change_class: Optional[ChangeClass] = None
    skip_reason: Optional[str] = None

    plan: Optional[InvestigationPlan] = None
    mid_cycle_decision: Optional[MidCycleDecision] = None
    evidence_ids: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)

    # Recursive-intelligence wiring (FR-8 / FR-14).
    #: When a promoted skill matched this incident's telemetry, its id/version —
    #: the investigation then legitimately runs leaner by reusing that validated
    #: procedural knowledge instead of re-deriving it.
    reused_skill_id: Optional[str] = None
    reused_skill_version: Optional[str] = None
    #: Steps skipped because a reused skill already encodes their conclusion.
    skipped_steps: list[str] = Field(default_factory=list)
    #: Learning outcome persisted after synthesis (skill_id/version/status/outcome).
    learning: Optional[dict[str, Any]] = None

    metrics: CycleMetrics = Field(default_factory=CycleMetrics)
    error: Optional[str] = None
    recovery_actions: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=utcnow)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES
