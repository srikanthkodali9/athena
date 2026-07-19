"""Every lifecycle transition and rejection path of learn_from_cycle."""

from __future__ import annotations

from pathlib import Path

from app.learning.schemas import LearningOutcome, RiskLevel, SkillStatus
from app.learning.service import LearningService
from app.security.contracts import InspectionStage

from tests.learning.conftest import (
    QuarantineGateway,
    make_redis_cycle,
    redis_evidence,
)


# -- Happy path: verified Redis incident promotes -------------------------


def test_verified_redis_cycle_promotes(service, store):
    cycle = make_redis_cycle(revise=True)
    result = service.learn_from_cycle(cycle, verified=True, evidence=redis_evidence())

    assert result.outcome is LearningOutcome.PROMOTED
    assert result.skill_status is SkillStatus.PROMOTED
    assert result.skill_id == "sre.redis.connection-saturation"
    assert result.validation_passes == 3
    # Episode + skill persisted.
    assert store.get_episode(result.episode_id) is not None
    skill = store.get_skill(result.skill_id, result.skill_version)
    assert skill.status is SkillStatus.PROMOTED
    assert skill.hiddenlayer_status == "passed"


def test_security_write_inspected_before_persistence(service, allow_gateway):
    service.learn_from_cycle(make_redis_cycle(), verified=True, evidence=redis_evidence())
    assert InspectionStage.SKILL_WRITE in allow_gateway.calls


# -- known_false_leads extraction from REVISE_PLAN ------------------------


def test_known_false_leads_extracted_from_revise_plan(service, store):
    cycle = make_redis_cycle(revise=True)
    result = service.learn_from_cycle(cycle, verified=True, evidence=redis_evidence())

    assert result.known_false_lead_count >= 1
    skill = store.get_skill(result.skill_id, result.skill_version)
    lead = skill.known_false_leads[0]
    assert lead.source == "revise_plan"
    # The abandoned approach is the ORIGINAL platform question.
    assert "What infrastructure change" in lead.approach
    # Invalidating evidence recorded (fact plane).
    assert lead.invalidated_by_evidence_ids
    assert "redis-incident-001" in lead.evidence_from_incidents


def test_no_false_leads_when_no_revision(service, store):
    cycle = make_redis_cycle(revise=False)
    result = service.learn_from_cycle(cycle, verified=True, evidence=redis_evidence())
    skill = store.get_skill(result.skill_id, result.skill_version)
    assert all(l.source != "revise_plan" for l in skill.known_false_leads)


# -- Rejection paths -------------------------------------------------------


def test_unverified_outcome_never_learns(service, store):
    result = service.learn_from_cycle(make_redis_cycle(), verified=False)
    assert result.outcome is LearningOutcome.SKIPPED_UNVERIFIED
    assert result.skill_id is None
    # Episode stored (audit) but NOT verified, and NO skill exists.
    ep = store.get_episode(result.episode_id)
    assert ep is not None and ep.verified is False
    assert store.list_skills() == []


def test_quarantine_rejects_persistence_and_is_unretrievable(store, tmp_path):
    svc = LearningService(
        store=store, security=QuarantineGateway(), skills_dir=str(tmp_path / "skills")
    )
    result = svc.learn_from_cycle(make_redis_cycle(), verified=True, evidence=redis_evidence())

    assert result.outcome is LearningOutcome.QUARANTINED
    skill = store.get_skill(result.skill_id, result.skill_version)
    assert skill.status is SkillStatus.QUARANTINED
    assert skill.hiddenlayer_status == "quarantined"
    # Quarantined markdown lives in the quarantined bucket, not validated.
    assert (tmp_path / "skills" / "quarantined").exists()
    assert not list((tmp_path / "skills" / "validated").glob("*.md")) if (tmp_path / "skills" / "validated").exists() else True


def test_replay_failure_blocks_promotion(service, store):
    # A generic (non-redis) verified cycle with no triggers cannot prove
    # discrimination => replay coverage fails => not promoted.
    cycle = make_redis_cycle(revise=False)
    # Overwrite synthesis so the redis family is NOT detected.
    for f in cycle.findings:
        f.hypothesis = "Unknown latency anomaly of indeterminate origin."
        f.interpretation = "" if f.role != "synthesis" else "no clear cause"
    result = service.learn_from_cycle(cycle, verified=True, evidence=redis_evidence())
    assert result.outcome is LearningOutcome.REJECTED_REPLAY
    assert result.skill_status is SkillStatus.SCANNED


def test_insufficient_evidence_retains_not_promotes(service, store):
    cycle = make_redis_cycle(revise=True, confidence=0.5)  # below promotion threshold
    result = service.learn_from_cycle(cycle, verified=True, evidence=redis_evidence())
    assert result.outcome is LearningOutcome.REJECTED_INSUFFICIENT_EVIDENCE
    assert result.skill_status is SkillStatus.VALIDATED  # replay passed, just weak


def test_weak_single_evidence_retains(service):
    cycle = make_redis_cycle(revise=True, evidence_ids=["ev_only-one"])
    result = service.learn_from_cycle(cycle, verified=True)
    assert result.outcome is LearningOutcome.REJECTED_INSUFFICIENT_EVIDENCE


def test_high_risk_requires_approval(service, store):
    cycle = make_redis_cycle(revise=True)
    result = service.learn_from_cycle(
        cycle, verified=True, evidence=redis_evidence(),
        requires_approval_override=True,
    )
    assert result.outcome is LearningOutcome.RETAINED_NEEDS_APPROVAL
    assert result.skill_status is SkillStatus.VALIDATED


# -- Versioning ------------------------------------------------------------


def test_relearning_same_family_bumps_version(service, store):
    r1 = service.learn_from_cycle(make_redis_cycle(), verified=True, evidence=redis_evidence())
    r2 = service.learn_from_cycle(make_redis_cycle(), verified=True, evidence=redis_evidence())
    assert r1.skill_version == "1.0.0"
    assert r2.skill_version == "1.1.0"
    # Provenance accumulates incidents across versions.
    v2 = store.get_skill(r2.skill_id, r2.skill_version)
    assert "redis-incident-001" in v2.provenance.created_from_incidents
