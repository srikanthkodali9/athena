"""Recursive learning subsystem (FR-7 / FR-8 / FR-9).

Converts a *verified* investigation cycle into protected, versioned procedural
knowledge — a candidate ``Skill`` that carries both the winning hypothesis chain
AND the ``known_false_leads`` (dead ends) discovered mid-cycle — validates it by
deterministic replay, gates persistence through the security ``SecurityGateway``
(``SKILL_WRITE``), and promotes / retains / rejects / quarantines it per policy.

Design invariants (AGENTS.md, PRD §13):

* Deterministic FACTS are kept separate from model INTERPRETATION in both
  ``Episode`` and ``Skill`` payloads.
* Generated skills and remembered episodes are treated as UNTRUSTED on future
  reads (retrieval never returns quarantined / deprecated / expired artifacts).
* Nothing is learned from an unverified outcome.
* Vector similarity is never sufficient validation — replay evaluates the skill's
  discrimination against positive / negative / contradictory fixtures.
* Source runbooks are never mutated; proposals are written separately.

Public entrypoints:

* :class:`app.learning.service.LearningService` — ``learn_from_cycle``.
* :class:`app.learning.store.LearningStore` — the episodes/skills/proposals/
  comparisons repository over the shared SQLite file.
* :class:`app.learning.retrieval.SkillRetriever` — the safe retrieval contract.
"""

from __future__ import annotations

from app.learning.schemas import (
    Episode,
    KnownFalseLead,
    LearningOutcome,
    LearningResult,
    ReplayKind,
    ReplayRecord,
    ReplayVerdict,
    RunbookProposal,
    Skill,
    SkillProvenance,
    SkillStatus,
    Comparison,
)

__all__ = [
    "Episode",
    "KnownFalseLead",
    "LearningOutcome",
    "LearningResult",
    "ReplayKind",
    "ReplayRecord",
    "ReplayVerdict",
    "RunbookProposal",
    "Skill",
    "SkillProvenance",
    "SkillStatus",
    "Comparison",
]
