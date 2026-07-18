"""Episode -> skill learning pipeline (FR-7 / FR-8 / FR-9).

``LearningService.learn_from_cycle`` is the single coordinator-facing entrypoint.
Given a terminal :class:`~app.athena.schemas.Cycle` and a verification verdict it:

1. Compresses the verified episode, keeping FACTS separate from INTERPRETATION.
2. Extracts the winning hypothesis chain AND ``known_false_leads`` from the
   ``REVISE_PLAN`` mid-cycle decision + disproved hypotheses.
3. Drafts a versioned CANDIDATE skill with provenance + risk.
4. Requests ``SKILL_WRITE`` security inspection BEFORE persistence; a quarantine
   verdict rejects persistence and the skill is stored quarantined + unretrievable.
5. Runs deterministic replay (positive / negative / contradictory) and records
   validation history.
6. Promotes / retains / rejects per policy — never promoting an unverified,
   weak, replay-failing, or approval-gated candidate.

The whole pipeline is synchronous and offline: it depends only on a
:class:`~app.learning.store.LearningStore` (its own SQLite tables) and a
:class:`~app.security.contracts.SecurityGateway` (a mock/NoOp is fine).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from app.athena.schemas import Cycle, Evidence, Finding, MidCycleDecision
from app.learning.markdown import write_skill_markdown
from app.learning.replay import (
    ReplayFixture,
    ReplayValidator,
    build_fixtures_for,
)
from app.learning.schemas import (
    Episode,
    EpisodeFacts,
    EpisodeInterpretation,
    KnownFalseLead,
    LearningOutcome,
    LearningResult,
    RiskLevel,
    Skill,
    SkillProvenance,
    SkillStatus,
)
from app.learning.store import LearningStore
from app.security.contracts import (
    InspectionRequest,
    InspectionStage,
    SecurityAction,
    SecurityGateway,
)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LearningPolicy:
    """Thresholds that gate promotion (PRD §13.3/§13.6).

    "No skill may be promoted from one weak episode or without evidence."
    """

    min_supporting_evidence: int = 2
    min_confidence: float = 0.85
    #: A high-risk skill always needs human approval before promotion.
    approval_required_risk: RiskLevel = RiskLevel.HIGH


def new_episode_id() -> str:
    return "ep_" + uuid.uuid4().hex[:12]


def new_skill_id_suffix() -> str:
    return uuid.uuid4().hex[:6]


def _next_version(previous: Optional[str]) -> str:
    if not previous:
        return "1.0.0"
    try:
        major, minor, patch = (int(p) for p in previous.split("."))
    except ValueError:
        return "1.0.0"
    return f"{major}.{minor + 1}.0"


# ---------------------------------------------------------------------------
# Pattern drafting (P0 supports the Redis connection-saturation family)
# ---------------------------------------------------------------------------

# Trigger vocabulary is aligned with the demo telemetry contract (fixtures/):
# incident one and two both expose ``redis_connected_clients`` with a ``limit``
# (a saturation RATIO) and an ``api`` 5xx ratio vs baseline — NOT a
# ``redis_rejected_connections`` counter. So the canonical PRD §13.4 trigger
# ``redis_rejected_connections > 0`` is expressed here as the derivable
# ``redis_saturation_ratio >= 0.9`` (documented source-of-truth deviation),
# while ``api_5xx_rate > baseline`` is kept verbatim. The core skill-reuse hook
# maps telemetry -> this signal dict so incident two retrieves this skill.
_REDIS_SKILL_ID = "sre.redis.connection-saturation"
_REDIS_TRIGGERS = ["redis_saturation_ratio >= 0.9", "api_5xx_rate > baseline"]
_REDIS_PRECONDITIONS = ["redis_metrics_available", "dependency_graph_contains_api_to_redis"]
_REDIS_STOP_CONDITIONS = [
    "timing_not_correlated",
    "redis_healthy_and_other_dependency_explains",
    "required_telemetry_unavailable",
]


@dataclass
class _PatternTemplate:
    skill_id: str
    domain: str
    title: str
    objective: str
    triggers: list[str]
    preconditions: list[str]
    procedure: list[str]
    stop_conditions: list[str]
    escalation: str
    risk: RiskLevel
    requires_approval: bool


def _is_redis_family(interpretation: EpisodeInterpretation, cycle: Cycle) -> bool:
    text = " ".join(
        [
            interpretation.winning_hypothesis,
            interpretation.recommendation,
            *interpretation.hypothesis_chain,
        ]
    ).lower()
    return "redis" in text and any(k in text for k in ("connection", "pool", "saturation"))


def _draft_pattern(interpretation: EpisodeInterpretation, cycle: Cycle) -> _PatternTemplate:
    """Map the verified interpretation onto a reusable skill template."""
    if _is_redis_family(interpretation, cycle):
        return _PatternTemplate(
            skill_id=_REDIS_SKILL_ID,
            domain="observability",
            title="Diagnose Redis Connection Saturation",
            objective=(
                "Determine whether API failures are caused by Redis connection "
                "exhaustion introduced by a deployment pool-size change."
            ),
            triggers=list(_REDIS_TRIGGERS),
            preconditions=list(_REDIS_PRECONDITIONS),
            procedure=[
                "Query approved Redis metrics (rejected connections, connected clients).",
                "Compare the current window against baseline.",
                "Confirm API 5xx growth follows Redis rejection growth in time.",
                "Check whether a deployment changed the connection-pool settings.",
                "Calculate affected downstream services.",
                "Recommend rollback or pool correction based on evidence.",
            ],
            stop_conditions=list(_REDIS_STOP_CONDITIONS),
            escalation="Escalate to the platform owner if pool settings changed without review.",
            risk=RiskLevel.MEDIUM,
            requires_approval=False,
        )
    # Generic fallback: unknown patterns are high-risk and approval-gated. They
    # rarely promote (curated fixtures do not exist), which is the intended
    # conservative default — we only auto-learn families we can validate.
    slug = "sre.generic." + new_skill_id_suffix()
    return _PatternTemplate(
        skill_id=slug,
        domain="observability",
        title=interpretation.winning_hypothesis[:80] or "Generic diagnostic",
        objective=interpretation.winning_hypothesis,
        triggers=[],
        preconditions=[],
        procedure=list(interpretation.hypothesis_chain) or ["Follow the verified evidence chain."],
        stop_conditions=["required_telemetry_unavailable"],
        escalation="Escalate to the service owner.",
        risk=RiskLevel.HIGH,
        requires_approval=True,
    )


# ---------------------------------------------------------------------------
# LearningService
# ---------------------------------------------------------------------------


class LearningService:
    """The episode->skill pipeline. Offline, deterministic, security-gated."""

    def __init__(
        self,
        *,
        store: LearningStore,
        security: SecurityGateway,
        skills_dir: str | Path = "skills",
        policy: Optional[LearningPolicy] = None,
    ) -> None:
        self._store = store
        self._security = security
        self._skills_dir = Path(skills_dir)
        self._policy = policy or LearningPolicy()
        self._replay = ReplayValidator()

    # -- Entrypoint --------------------------------------------------------

    def learn_from_cycle(
        self,
        cycle: Cycle,
        verified: bool,
        *,
        evidence: Optional[Sequence[Evidence]] = None,
        fixtures: Optional[list[ReplayFixture]] = None,
        risk_override: Optional[RiskLevel] = None,
        requires_approval_override: Optional[bool] = None,
    ) -> LearningResult:
        """Convert one verified cycle into procedural knowledge.

        ``evidence`` (optional) lets the caller supply the round-1 evidence for
        richer provenance; otherwise ``cycle.evidence_ids`` is used. ``fixtures``
        overrides the replay set (defaults to the family-derived set). The two
        overrides let a coordinator inject an approval-required or higher-risk
        policy without re-drafting.
        """
        reasons: list[str] = []
        episode = self._compress_episode(cycle, verified, evidence)

        # (1) Never learn from an unverified outcome (FR8-02). Store for audit.
        if not verified:
            self._store.save_episode(episode)
            reasons.append("outcome not verified — no skill drafted")
            return LearningResult(
                outcome=LearningOutcome.SKIPPED_UNVERIFIED,
                episode_id=episode.episode_id,
                known_false_lead_count=len(episode.known_false_leads),
                reasons=reasons,
            )

        self._store.save_episode(episode)

        # (2) Draft a versioned CANDIDATE skill with provenance + risk.
        skill = self._draft_candidate(
            episode, cycle, risk_override, requires_approval_override
        )

        # (3) Security gate BEFORE persistence (SKILL_WRITE, fail closed).
        decision = self._security.inspect(
            InspectionRequest(
                stage=InspectionStage.SKILL_WRITE,
                producer="learning.service",
                consumer="skill_store",
                content=self._skill_inspection_content(skill),
                cycle_id=cycle.cycle_id,
                metadata={"skill_id": skill.skill_id, "version": skill.version},
            )
        )
        skill.security_event_id = decision.security_event_id
        if decision.action not in (SecurityAction.ALLOW, SecurityAction.REDACT):
            skill.status = SkillStatus.QUARANTINED
            skill.hiddenlayer_status = "quarantined"
            path = self._persist(skill)
            reasons.append(
                f"security rejected persistence ({decision.action.value}); quarantined"
            )
            return self._result(LearningOutcome.QUARANTINED, episode, skill, decision, reasons, path)
        skill.status = SkillStatus.SCANNED
        skill.hiddenlayer_status = "passed"

        # (4) Deterministic replay validation (records history either way).
        skill.status = SkillStatus.SHADOW
        replay_set = fixtures or build_fixtures_for(skill)
        report = self._replay.run(skill, replay_set)
        skill.validation_history = report.records
        skill.validation_passes = report.passes

        if not report.covers_all_kinds:
            # Scanned but failed to prove discrimination — retain, do not promote.
            skill.status = SkillStatus.SCANNED
            path = self._persist(skill)
            reasons.append(
                "replay did not pass positive+negative+contradictory — not promotable"
            )
            return self._result(LearningOutcome.REJECTED_REPLAY, episode, skill, decision, reasons, path)
        skill.status = SkillStatus.VALIDATED

        # (5) Evidence + confidence sufficiency (no promotion from a weak episode).
        evidence_ok = (
            len(skill.provenance.supporting_evidence_ids) >= self._policy.min_supporting_evidence
            and bool(skill.provenance.created_from_incidents)
        )
        if not evidence_ok or skill.confidence < self._policy.min_confidence:
            path = self._persist(skill)
            reasons.append(
                "insufficient evidence or confidence below promotion threshold — retained as validated"
            )
            return self._result(
                LearningOutcome.REJECTED_INSUFFICIENT_EVIDENCE, episode, skill, decision, reasons, path
            )

        # (6) Risk / approval gate.
        if skill.requires_approval or skill.risk is self._policy.approval_required_risk:
            path = self._persist(skill)
            reasons.append("high-risk or approval-required — retained pending human approval")
            return self._result(
                LearningOutcome.RETAINED_NEEDS_APPROVAL, episode, skill, decision, reasons, path
            )

        # (7) Promote.
        skill.status = SkillStatus.PROMOTED
        path = self._persist(skill)
        reasons.append("verified + scanned + replay-validated + sufficient evidence — promoted")
        return self._result(LearningOutcome.PROMOTED, episode, skill, decision, reasons, path)

    # -- Episode compression ----------------------------------------------

    def _compress_episode(
        self, cycle: Cycle, verified: bool, evidence: Optional[Sequence[Evidence]]
    ) -> Episode:
        incident_id = self._incident_id(cycle)
        evidence_ids = (
            [e.id for e in evidence] if evidence else list(cycle.evidence_ids)
        )
        facts = EpisodeFacts(
            incident_id=incident_id,
            service=self._guess_service(cycle),
            cycle_id=cycle.cycle_id,
            signals_checked=list(cycle.signals_checked),
            evidence_ids=evidence_ids,
            changes_detected=list(cycle.changes_detected),
            metrics={
                "model_calls": cycle.metrics.model_calls,
                "tool_calls": cycle.metrics.tool_calls,
                "specialists_invoked": cycle.metrics.specialists_invoked,
                "total_tokens": cycle.metrics.total_tokens,
                "duration_ms": cycle.metrics.duration_ms,
            },
        )
        interp = self._interpret(cycle)
        return Episode(
            episode_id=new_episode_id(),
            incident_id=incident_id,
            cycle_id=cycle.cycle_id,
            outcome=cycle.decision.value if cycle.decision else "UNKNOWN",
            verified=verified,
            facts=facts,
            interpretation=interp,
            known_false_leads=self._extract_known_false_leads(cycle, incident_id),
        )

    def _interpret(self, cycle: Cycle) -> EpisodeInterpretation:
        synthesis = _find_role(cycle.findings, "synthesis")
        chain = [f.hypothesis for f in cycle.findings if f.hypothesis]
        plan = cycle.plan
        return EpisodeInterpretation(
            winning_hypothesis=synthesis.hypothesis if synthesis else (chain[-1] if chain else ""),
            hypothesis_chain=chain,
            recommendation=(synthesis.interpretation if synthesis else ""),
            confidence=synthesis.confidence if synthesis else 0.0,
            original_question=plan.original_platform_question if plan else None,
            revised_question=plan.platform_question if plan else None,
            revision_reason=plan.revision_reason if plan else None,
        )

    def _extract_known_false_leads(
        self, cycle: Cycle, incident_id: Optional[str]
    ) -> list[KnownFalseLead]:
        """Negative knowledge from REVISE_PLAN + disproved hypotheses (§13.1a)."""
        leads: list[KnownFalseLead] = []
        plan = cycle.plan
        # Primary source: the one permitted mid-cycle REVISE_PLAN.
        if (
            cycle.mid_cycle_decision is MidCycleDecision.REVISE_PLAN
            and plan is not None
            and plan.revision_used
            and plan.original_platform_question
        ):
            obs = _find_role(cycle.findings, "observability")
            invalidating = (
                list(obs.cited_evidence_ids) if obs and obs.cited_evidence_ids
                else list(cycle.evidence_ids)
            )
            leads.append(
                KnownFalseLead(
                    approach=plan.original_platform_question,
                    failure_reason=(
                        plan.revision_reason
                        or "Round-1 evidence redirected the investigation to another component."
                    ),
                    invalidated_by_evidence_ids=invalidating,
                    evidence_from_incidents=[incident_id] if incident_id else [],
                    source="revise_plan",
                )
            )
        # Secondary source: an explicitly disproved (very low confidence) hypothesis
        # that differs from the winning one.
        synthesis = _find_role(cycle.findings, "synthesis")
        winning = synthesis.hypothesis if synthesis else None
        for f in cycle.findings:
            if f.role in ("observability", "platform") and f.hypothesis and f.confidence <= 0.2:
                if winning and f.hypothesis == winning:
                    continue
                if any(f.hypothesis == lead.approach for lead in leads):
                    continue
                leads.append(
                    KnownFalseLead(
                        approach=f.hypothesis,
                        failure_reason="Hypothesis scored below the support floor and was abandoned.",
                        invalidated_by_evidence_ids=list(f.cited_evidence_ids),
                        evidence_from_incidents=[incident_id] if incident_id else [],
                        source="disproved_hypothesis",
                    )
                )
        return leads

    # -- Candidate drafting ------------------------------------------------

    def _draft_candidate(
        self,
        episode: Episode,
        cycle: Cycle,
        risk_override: Optional[RiskLevel],
        requires_approval_override: Optional[bool],
    ) -> Skill:
        tpl = _draft_pattern(episode.interpretation, cycle)
        previous = self._store.latest_version(tpl.skill_id)
        version = _next_version(previous)
        prior_incidents = self._prior_incidents(tpl.skill_id)
        this_incident = [episode.facts.incident_id] if episode.facts.incident_id else []
        created_from = _dedupe(prior_incidents + this_incident)

        skill = Skill(
            skill_id=tpl.skill_id,
            version=version,
            status=SkillStatus.CANDIDATE,
            domain=tpl.domain,
            title=tpl.title,
            objective=tpl.objective,
            triggers=tpl.triggers,
            preconditions=tpl.preconditions,
            procedure=tpl.procedure,
            stop_conditions=tpl.stop_conditions,
            escalation=tpl.escalation,
            risk=risk_override or tpl.risk,
            requires_approval=(
                requires_approval_override
                if requires_approval_override is not None
                else tpl.requires_approval
            ),
            provenance=SkillProvenance(
                created_from_incidents=created_from,
                created_from_episodes=[episode.episode_id],
                supporting_evidence_ids=list(episode.facts.evidence_ids),
            ),
            known_false_leads=episode.known_false_leads,
            confidence=episode.interpretation.confidence,
        )
        return skill

    def _prior_incidents(self, skill_id: str) -> list[str]:
        incidents: list[str] = []
        for prev in self._store.get_skill_versions(skill_id):
            incidents.extend(prev.provenance.created_from_incidents)
        return incidents

    # -- Persistence -------------------------------------------------------

    def _persist(self, skill: Skill) -> Path:
        self._store.save_skill(skill)
        return write_skill_markdown(self._skills_dir, skill)

    def _result(self, outcome, episode, skill, decision, reasons, skill_path) -> LearningResult:
        return LearningResult(
            outcome=outcome,
            episode_id=episode.episode_id,
            skill_id=skill.skill_id,
            skill_version=skill.version,
            skill_status=skill.status,
            known_false_lead_count=len(skill.known_false_leads),
            validation_passes=skill.validation_passes,
            validation_history=skill.validation_history,
            security_action=decision.action.value if decision else None,
            security_event_id=skill.security_event_id,
            skill_path=str(skill_path),
            reasons=reasons,
        )

    # -- Small deterministic helpers ---------------------------------------

    def _incident_id(self, cycle: Cycle) -> Optional[str]:
        if cycle.plan and cycle.plan.incident_ref:
            return cycle.plan.incident_ref
        return cycle.fixture

    def _guess_service(self, cycle: Cycle) -> Optional[str]:
        for change in cycle.changes_detected:
            svc = change.get("service") if isinstance(change, dict) else None
            if svc:
                return svc
        return None

    def _skill_inspection_content(self, skill: Skill) -> str:
        parts = [skill.title, skill.objective]
        parts += skill.procedure
        parts += [lead.approach + " " + lead.failure_reason for lead in skill.known_false_leads]
        return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# module helpers
# ---------------------------------------------------------------------------


def _find_role(findings: Sequence[Finding], role: str) -> Optional[Finding]:
    for f in findings:
        if f.role == role:
            return f
    return None


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out
