"""Learning-plane schemas (FR-7 / FR-8 / FR-9), Pydantic v2.

These are the *procedural / episodic* memory records that ADR-0002 maps to the
``episodes``, ``skills``, ``runbook_proposals``, and ``comparisons`` tables. They
are deliberately separate from the CORE domain schemas in
``app/athena/schemas.py`` (which this module only consumes read-only).

Fact / interpretation separation (AGENTS.md rule; FR6-03):

* :class:`EpisodeFacts` — deterministic evidence IDs, signals, and metrics.
* :class:`EpisodeInterpretation` — the model's winning hypothesis chain and the
  ``known_false_leads`` it abandoned. Never treated as fact on its own.

Every promoted artifact is versioned and carries provenance + security +
validation history so it can be rolled back and audited (PRD §13.6).
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


class SkillStatus(str, enum.Enum):
    """Skill lifecycle (PRD §13.3; ADR-0002 ``skills.status``).

    ``observed`` -> ``candidate`` -> ``scanned`` -> ``shadow`` -> ``validated``
    -> ``promoted``, with ``deprecated`` / ``quarantined`` as terminal sinks.
    Only ``promoted`` skills are retrievable (FR8-08).
    """

    OBSERVED = "observed"
    CANDIDATE = "candidate"
    SCANNED = "scanned"
    SHADOW = "shadow"
    VALIDATED = "validated"
    PROMOTED = "promoted"
    DEPRECATED = "deprecated"
    QUARANTINED = "quarantined"


#: Statuses that MUST NOT surface through retrieval or feed later prompts.
UNRETRIEVABLE_STATUSES: frozenset[SkillStatus] = frozenset(
    {
        SkillStatus.OBSERVED,
        SkillStatus.CANDIDATE,
        SkillStatus.SCANNED,
        SkillStatus.SHADOW,
        SkillStatus.VALIDATED,  # validated-but-not-yet-promoted is not reusable
        SkillStatus.DEPRECATED,
        SkillStatus.QUARANTINED,
    }
)


class LearningOutcome(str, enum.Enum):
    """Terminal outcome of one ``learn_from_cycle`` call."""

    SKIPPED_UNVERIFIED = "skipped_unverified"
    QUARANTINED = "quarantined"
    REJECTED_REPLAY = "rejected_replay"
    REJECTED_INSUFFICIENT_EVIDENCE = "rejected_insufficient_evidence"
    RETAINED_NEEDS_APPROVAL = "retained_needs_approval"
    PROMOTED = "promoted"


class ReplayKind(str, enum.Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    CONTRADICTORY = "contradictory"


class ReplayVerdict(str, enum.Enum):
    """What the skill decided when replayed against a fixture."""

    APPLIED = "applied"  # triggers + preconditions met, no stop -> concludes
    NOT_APPLICABLE = "not_applicable"  # triggers/preconditions unmet -> abstains
    STOPPED = "stopped"  # triggers met but a stop condition fired -> abstains


class ProposalValidationStatus(str, enum.Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    REJECTED = "rejected"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ---------------------------------------------------------------------------
# Episode (episodic memory) — facts vs interpretation kept separate
# ---------------------------------------------------------------------------


class KnownFalseLead(BaseModel):
    """A dead end Athena explored and abandoned (negative knowledge, §13.1a).

    Extracted from ``REVISE_PLAN`` mid-cycle decisions and disproved hypotheses.
    Encoding *what not to check* is as valuable as the winning path.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = SCHEMA_VERSION
    approach: str = Field(description="The hypothesis / question that was abandoned.")
    failure_reason: str = Field(description="Why the evidence invalidated it.")
    #: Deterministic evidence IDs that invalidated the approach (FACT plane).
    invalidated_by_evidence_ids: list[str] = Field(default_factory=list)
    #: Incident IDs where this dead end was observed.
    evidence_from_incidents: list[str] = Field(default_factory=list)
    #: Where it came from: ``revise_plan`` | ``disproved_hypothesis``.
    source: str = "revise_plan"


class EpisodeFacts(BaseModel):
    """Deterministic plane of an episode (never model opinion)."""

    incident_id: Optional[str] = None
    service: Optional[str] = None
    cycle_id: str
    signals_checked: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    changes_detected: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class EpisodeInterpretation(BaseModel):
    """Model-interpretation plane of an episode (hypotheses, not facts)."""

    winning_hypothesis: str = ""
    hypothesis_chain: list[str] = Field(default_factory=list)
    recommendation: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    #: The original -> revised platform question path (§13.1a persistence rule).
    original_question: Optional[str] = None
    revised_question: Optional[str] = None
    revision_reason: Optional[str] = None


class Episode(BaseModel):
    """A compressed verified investigation record (ADR-0002 ``episodes``).

    ``verified`` gates learning: an unverified episode may be stored for audit
    but never yields a promotable skill (FR8-02).
    """

    schema_version: str = SCHEMA_VERSION
    episode_id: str
    incident_id: Optional[str] = None
    cycle_id: str
    outcome: str = Field(description="ESCALATE | INVESTIGATE | CONTAIN | ...")
    verified: bool = False
    facts: EpisodeFacts
    interpretation: EpisodeInterpretation
    known_false_leads: list[KnownFalseLead] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def compressed(self) -> dict[str, Any]:
        """The ``compressed_json`` payload persisted in the episodes table."""
        return {
            "schema_version": self.schema_version,
            "facts": self.facts.model_dump(mode="json"),
            "interpretation": self.interpretation.model_dump(mode="json"),
        }


# ---------------------------------------------------------------------------
# Skill (procedural memory) — versioned, provenance-carrying, security-gated
# ---------------------------------------------------------------------------


class SkillProvenance(BaseModel):
    """Where a skill came from — the audit trail behind every procedural claim."""

    created_from_incidents: list[str] = Field(default_factory=list)
    created_from_episodes: list[str] = Field(default_factory=list)
    #: Deterministic evidence IDs that support the winning procedure (FACT plane).
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    author: str = "athena.learning"


class ReplayRecord(BaseModel):
    """One replay-validation result, retained as history (FR8-06)."""

    schema_version: str = SCHEMA_VERSION
    fixture_name: str
    kind: ReplayKind
    verdict: ReplayVerdict
    passed: bool
    detail: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Skill(BaseModel):
    """Versioned procedural memory (ADR-0002 ``skills``; PRD §13.4).

    Facts vs interpretation: ``triggers`` / ``preconditions`` / ``stop_conditions``
    are objective match rules; ``procedure`` is the model-derived method. The skill
    is UNTRUSTED on read — retrieval and prompting always re-check status.
    """

    schema_version: str = SCHEMA_VERSION
    skill_id: str = Field(description="Stable family id, e.g. 'sre.redis.connection-saturation'.")
    version: str = "1.0.0"
    status: SkillStatus = SkillStatus.CANDIDATE
    domain: str = "observability"

    title: str = ""
    objective: str = ""
    triggers: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    procedure: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    escalation: str = ""

    risk: RiskLevel = RiskLevel.MEDIUM
    requires_approval: bool = False

    provenance: SkillProvenance = Field(default_factory=SkillProvenance)
    known_false_leads: list[KnownFalseLead] = Field(default_factory=list)

    # Security + validation history.
    hiddenlayer_status: str = "pending"  # pending | passed | quarantined | failed
    security_event_id: Optional[str] = None
    validation_passes: int = 0
    validation_history: list[ReplayRecord] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)

    #: Environment-specific facts expire (PRD §13.6). ``None`` => non-expiring.
    expires_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    # -- Derived helpers ---------------------------------------------------

    def is_expired(self, *, now: Optional[datetime] = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or utcnow()) >= self.expires_at

    def is_retrievable(self, *, now: Optional[datetime] = None) -> bool:
        """Only a promoted, non-expired, non-quarantined skill is reusable."""
        return self.status is SkillStatus.PROMOTED and not self.is_expired(now=now)

    @property
    def created_from_incidents(self) -> list[str]:
        return list(self.provenance.created_from_incidents)


# ---------------------------------------------------------------------------
# Runbook proposal (FR-9) — never mutates a source runbook
# ---------------------------------------------------------------------------


class RunbookProposal(BaseModel):
    """A reviewable patch against a human-owned runbook (ADR-0002 ``runbook_proposals``)."""

    schema_version: str = SCHEMA_VERSION
    proposal_id: str
    source_ref: str = Field(description="Path/ref of the source runbook (read-only).")
    source_hash: str = Field(description="Hash of the source at proposal time (mutation guard).")
    kind: str = Field(default="add_step", description="add_step | rollback | stale_warning | ownership | new_runbook")
    title: str = ""
    diff: str = Field(description="Unified-diff-style reviewable patch.")
    provenance: dict[str, Any] = Field(default_factory=dict)
    validation_status: ProposalValidationStatus = ProposalValidationStatus.DRAFT
    hiddenlayer_status: str = "pending"
    security_event_id: Optional[str] = None
    risk: RiskLevel = RiskLevel.MEDIUM
    required_reviewer: str = "platform_owner"
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Comparison (FR-14) — first-vs-learned deltas
# ---------------------------------------------------------------------------


class Comparison(BaseModel):
    """First-incident vs learned-incident metric deltas (ADR-0002 ``comparisons``)."""

    schema_version: str = SCHEMA_VERSION
    comparison_id: str
    first_incident_id: str
    learned_incident_id: str
    deltas: dict[str, Any] = Field(default_factory=dict)
    #: The exact skill + evidence the learned incident reused (FR-14 citation).
    reused_skill_id: Optional[str] = None
    reused_skill_version: Optional[str] = None
    reused_evidence_ids: list[str] = Field(default_factory=list)
    improved_metric_count: int = 0
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# LearningResult — what learn_from_cycle returns to the coordinator
# ---------------------------------------------------------------------------


class LearningResult(BaseModel):
    """Structured outcome of one learning attempt (coordinator-facing)."""

    schema_version: str = SCHEMA_VERSION
    outcome: LearningOutcome
    episode_id: Optional[str] = None
    skill_id: Optional[str] = None
    skill_version: Optional[str] = None
    skill_status: Optional[SkillStatus] = None
    known_false_lead_count: int = 0
    validation_passes: int = 0
    validation_history: list[ReplayRecord] = Field(default_factory=list)
    security_action: Optional[str] = None
    security_event_id: Optional[str] = None
    runbook_proposal_id: Optional[str] = None
    skill_path: Optional[str] = None
    reasons: list[str] = Field(default_factory=list)

    @property
    def promoted(self) -> bool:
        return self.outcome is LearningOutcome.PROMOTED
