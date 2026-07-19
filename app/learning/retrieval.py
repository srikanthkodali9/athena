"""Safe skill retrieval contract (FR8-08, FR-14 citation support).

Retrieval is a trust boundary: stored skills are UNTRUSTED on read. This module
is the ONLY sanctioned way later heartbeats obtain a reusable skill, and it
guarantees:

* Only ``promoted`` skills are returned (candidate/scanned/shadow/validated are
  not yet reusable; deprecated/quarantined are never reusable).
* Expired skills (environment-specific facts past their TTL) are excluded.
* The newest promoted version of a family wins.
* A match records EXACTLY which skill version + supporting evidence it reused, so
  a learned incident can cite its provenance (FR-14).

Matching uses the same deterministic trigger grammar as replay — never vector
similarity alone (that is explicitly insufficient validation, PRD §13.6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.learning.replay import _trigger_met, ReplayFixture
from app.learning.schemas import ReplayKind, Skill, SkillStatus
from app.learning.store import LearningStore


@dataclass
class SkillCitation:
    """Proof of exactly what was reused (FR-14)."""

    skill_id: str
    version: str
    matched_triggers: list[str] = field(default_factory=list)
    reused_evidence_ids: list[str] = field(default_factory=list)
    known_false_leads: list[str] = field(default_factory=list)


class SkillRetriever:
    """Returns only promoted, non-expired, non-quarantined skills."""

    def __init__(self, store: LearningStore) -> None:
        self._store = store

    def _retrievable(self, *, now: Optional[datetime] = None) -> list[Skill]:
        out: list[Skill] = []
        for skill in self._store.list_skills(status=SkillStatus.PROMOTED):
            if skill.is_retrievable(now=now):
                out.append(skill)
        return out

    def latest_promoted(self, skill_id: str, *, now: Optional[datetime] = None) -> Optional[Skill]:
        """Newest promoted, non-expired version of one family, or ``None``."""
        candidates = [
            s
            for s in self._store.get_skill_versions(skill_id)
            if s.is_retrievable(now=now)
        ]
        candidates.sort(key=lambda s: s.created_at)
        return candidates[-1] if candidates else None

    def match(
        self, signals: dict[str, float], *, baselines: Optional[dict[str, float]] = None,
        preconditions_met: Optional[set[str]] = None, now: Optional[datetime] = None,
    ) -> Optional[tuple[Skill, SkillCitation]]:
        """Find the best retrievable skill whose triggers fire on ``signals``.

        Returns the skill and a :class:`SkillCitation` documenting the reuse, or
        ``None`` when nothing applicable is promoted (incident-two entrypoint).
        """
        fixture = ReplayFixture(
            name="live",
            kind=ReplayKind.POSITIVE,
            signals=signals,
            baselines=baselines or {},
            preconditions_met=frozenset(preconditions_met or set()),
        )
        best: Optional[Skill] = None
        best_matched: list[str] = []
        for skill in self._retrievable(now=now):
            if not skill.triggers:
                continue
            matched = [t for t in skill.triggers if _trigger_met(t, fixture)]
            if len(matched) != len(skill.triggers):
                continue
            if any(p not in fixture.preconditions_met for p in skill.preconditions):
                continue
            # Prefer the higher-confidence / newer match.
            if best is None or (skill.confidence, skill.created_at) > (
                best.confidence,
                best.created_at,
            ):
                best, best_matched = skill, matched
        if best is None:
            return None
        citation = SkillCitation(
            skill_id=best.skill_id,
            version=best.version,
            matched_triggers=best_matched,
            reused_evidence_ids=list(best.provenance.supporting_evidence_ids),
            known_false_leads=[lead.approach for lead in best.known_false_leads],
        )
        return best, citation
