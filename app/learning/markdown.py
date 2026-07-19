"""Skill markdown-with-frontmatter renderer (PRD §13.4).

The DB is the authoritative index; the markdown file is the human-reviewable
artifact. A skill's on-disk bucket encodes its lifecycle at a glance:

* ``candidate`` / ``scanned`` / ``shadow`` -> ``skills/candidates/``
* ``validated`` / ``promoted``            -> ``skills/validated/``
* ``quarantined``                         -> ``skills/quarantined/``
* ``deprecated``                          -> ``skills/deprecated/``

A quarantined or deprecated file lives in an isolated bucket and is NEVER read
back into prompts or retrieval — the DB status is the enforcement point; the
directory split is a reviewer convenience and a defence in depth.

No YAML dependency: frontmatter is emitted deterministically so reset/replay
produce byte-identical files.
"""

from __future__ import annotations

from pathlib import Path

from app.learning.schemas import Skill, SkillStatus

_BUCKETS = {
    SkillStatus.OBSERVED: "candidates",
    SkillStatus.CANDIDATE: "candidates",
    SkillStatus.SCANNED: "candidates",
    SkillStatus.SHADOW: "candidates",
    SkillStatus.VALIDATED: "validated",
    SkillStatus.PROMOTED: "validated",
    SkillStatus.QUARANTINED: "quarantined",
    SkillStatus.DEPRECATED: "deprecated",
}


def bucket_for(status: SkillStatus) -> str:
    return _BUCKETS[status]


def _yaml_list(items: list[str], indent: str = "  ") -> list[str]:
    return [f"{indent}- {item}" for item in items] if items else []


def render_skill_markdown(skill: Skill) -> str:
    """Render the PRD §13.4 markdown-with-frontmatter document for a skill."""
    fm: list[str] = ["---"]
    fm.append(f"id: {skill.skill_id}")
    fm.append(f"version: {skill.version}")
    fm.append(f"status: {skill.status.value}")
    fm.append(f"domain: {skill.domain}")
    fm.append("triggers:")
    fm += _yaml_list(skill.triggers)
    fm.append("preconditions:")
    fm += _yaml_list(skill.preconditions)
    fm.append(f"risk: {skill.risk.value}")
    fm.append(f"requires_approval: {str(skill.requires_approval).lower()}")
    fm.append("created_from_incidents:")
    fm += _yaml_list(skill.provenance.created_from_incidents)
    fm.append(f"hiddenlayer_status: {skill.hiddenlayer_status}")
    fm.append(f"validation_passes: {skill.validation_passes}")
    fm.append(f"confidence: {skill.confidence}")
    fm.append("known_false_leads:")
    for lead in skill.known_false_leads:
        fm.append(f'  - approach: "{lead.approach}"')
        fm.append(f'    failure_reason: "{lead.failure_reason}"')
        fm.append("    evidence_from_incidents:")
        fm += _yaml_list(lead.evidence_from_incidents, indent="      ")
    fm.append("---")

    body: list[str] = ["", f"# {skill.title or skill.skill_id}", ""]
    if skill.objective:
        body += ["## Objective", "", skill.objective, ""]
    if skill.procedure:
        body += ["## Procedure", ""]
        body += [f"{i}. {step}" for i, step in enumerate(skill.procedure, start=1)]
        body += [""]
    if skill.stop_conditions:
        body += ["## Stop Conditions", ""]
        body += [f"- {sc}" for sc in skill.stop_conditions]
        body += [""]
    if skill.known_false_leads:
        body += ["## Known False Leads", ""]
        for lead in skill.known_false_leads:
            body += [f"- **{lead.approach}** — {lead.failure_reason}"]
        body += [""]
    if skill.escalation:
        body += ["## Escalation", "", skill.escalation, ""]

    return "\n".join(fm + body).rstrip() + "\n"


def skill_file_path(skills_root: Path, skill: Skill) -> Path:
    bucket = bucket_for(skill.status)
    return skills_root / bucket / f"{skill.skill_id}@{skill.version}.md"


def write_skill_markdown(skills_root: Path, skill: Skill) -> Path:
    """Write the skill markdown to its lifecycle bucket and return the path.

    When the status has moved buckets since a previous write, the stale file in
    the old bucket is removed so a quarantined/deprecated artifact never lingers
    in a retrievable bucket.
    """
    target = skill_file_path(skills_root, skill)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Remove any stale copies in other buckets for this exact version.
    for bucket in set(_BUCKETS.values()):
        stale = skills_root / bucket / f"{skill.skill_id}@{skill.version}.md"
        if stale != target and stale.exists():
            stale.unlink()
    target.write_text(render_skill_markdown(skill), encoding="utf-8")
    return target
