"""Skill markdown rendering + bucket placement (PRD §13.4)."""

from __future__ import annotations

from app.learning.markdown import bucket_for, render_skill_markdown, write_skill_markdown
from app.learning.schemas import (
    KnownFalseLead,
    RiskLevel,
    Skill,
    SkillProvenance,
    SkillStatus,
)


def _skill(status=SkillStatus.PROMOTED):
    return Skill(
        skill_id="sre.redis.connection-saturation",
        version="1.0.0",
        status=status,
        triggers=["redis_saturation_ratio >= 0.9", "api_5xx_rate > baseline"],
        preconditions=["redis_metrics_available"],
        procedure=["Query approved Redis metrics.", "Compare with baseline."],
        stop_conditions=["redis_healthy_and_other_dependency_explains"],
        risk=RiskLevel.MEDIUM,
        provenance=SkillProvenance(created_from_incidents=["redis-incident-001"]),
        known_false_leads=[
            KnownFalseLead(
                approach="Investigate source service internals first",
                failure_reason="Errors originate at the downstream Redis dependency.",
                evidence_from_incidents=["redis-incident-001"],
            )
        ],
        confidence=0.91,
        validation_passes=3,
        hiddenlayer_status="passed",
    )


def test_frontmatter_and_body_present():
    md = render_skill_markdown(_skill())
    assert md.startswith("---\n")
    assert "id: sre.redis.connection-saturation" in md
    assert "status: promoted" in md
    assert "known_false_leads:" in md
    assert "Investigate source service internals first" in md
    assert "## Procedure" in md
    assert "## Stop Conditions" in md


def test_bucket_mapping():
    assert bucket_for(SkillStatus.PROMOTED) == "validated"
    assert bucket_for(SkillStatus.CANDIDATE) == "candidates"
    assert bucket_for(SkillStatus.QUARANTINED) == "quarantined"
    assert bucket_for(SkillStatus.DEPRECATED) == "deprecated"


def test_status_change_moves_file_out_of_retrievable_bucket(tmp_path):
    skill = _skill(status=SkillStatus.PROMOTED)
    p1 = write_skill_markdown(tmp_path, skill)
    assert "validated" in str(p1) and p1.exists()

    skill.status = SkillStatus.QUARANTINED
    p2 = write_skill_markdown(tmp_path, skill)
    assert "quarantined" in str(p2) and p2.exists()
    # The stale validated copy must be gone — quarantined artifact never lingers.
    assert not p1.exists()


def test_render_is_deterministic():
    assert render_skill_markdown(_skill()) == render_skill_markdown(_skill())
