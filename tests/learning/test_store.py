"""LearningStore: table roundtrips, versioning, and shared-file coexistence."""

from __future__ import annotations

from app.learning.schemas import (
    Comparison,
    Episode,
    EpisodeFacts,
    EpisodeInterpretation,
    RunbookProposal,
    Skill,
    SkillStatus,
)
from app.learning.store import LearningStore
from app.memory.store import CycleStore


def _episode(cycle_id="cycle_1", verified=True):
    return Episode(
        episode_id="ep_test1",
        incident_id="redis-incident-001",
        cycle_id=cycle_id,
        outcome="ESCALATE",
        verified=verified,
        facts=EpisodeFacts(cycle_id=cycle_id, evidence_ids=["ev_a", "ev_b"]),
        interpretation=EpisodeInterpretation(winning_hypothesis="redis saturation"),
    )


def test_episode_roundtrip(store):
    ep = _episode()
    store.save_episode(ep)
    got = store.get_episode("ep_test1")
    assert got is not None
    assert got.verified is True
    assert got.facts.evidence_ids == ["ev_a", "ev_b"]
    assert got.compressed["facts"]["evidence_ids"] == ["ev_a", "ev_b"]


def test_verified_only_filter(store):
    store.save_episode(_episode("cycle_1", verified=True))
    store.save_episode(
        Episode(**{**_episode("cycle_2", verified=False).model_dump(), "episode_id": "ep_test2"})
    )
    assert len(store.list_episodes(verified_only=True)) == 1
    assert len(store.list_episodes()) == 2


def test_skill_versioning(store):
    s1 = Skill(skill_id="sre.redis.x", version="1.0.0", triggers=["a > 0"])
    s2 = Skill(skill_id="sre.redis.x", version="1.1.0", triggers=["a > 0"])
    store.save_skill(s1)
    store.save_skill(s2)
    assert store.latest_version("sre.redis.x") == "1.1.0"
    assert len(store.get_skill_versions("sre.redis.x")) == 2
    assert store.get_skill("sre.redis.x", "1.0.0").version == "1.0.0"


def test_list_skills_by_status(store):
    store.save_skill(Skill(skill_id="a", version="1.0.0", status=SkillStatus.PROMOTED))
    store.save_skill(Skill(skill_id="b", version="1.0.0", status=SkillStatus.QUARANTINED))
    promoted = store.list_skills(status=SkillStatus.PROMOTED)
    assert [s.skill_id for s in promoted] == ["a"]


def test_runbook_and_comparison_roundtrip(store):
    p = RunbookProposal(
        proposal_id="rbp_1", source_ref="runbook.md", source_hash="sha256:x", diff="--- a\n+++ b\n"
    )
    store.save_runbook_proposal(p)
    assert store.get_runbook_proposal("rbp_1").source_ref == "runbook.md"

    c = Comparison(
        comparison_id="cmp_1", first_incident_id="inc_1", learned_incident_id="inc_2",
        deltas={"tool_calls": -3}, improved_metric_count=2,
    )
    store.save_comparison(c)
    assert store.get_comparison("cmp_1").deltas["tool_calls"] == -3


def test_learning_and_core_share_one_db_file(tmp_path):
    """ADR-0002: learning opens the SAME sqlite file as core, own tables."""
    db = str(tmp_path / "athena.db")
    core = CycleStore(db)
    learning = LearningStore(db)
    try:
        learning.save_episode(_episode())
        assert learning.get_episode("ep_test1") is not None
        # Core's tables are untouched and usable in the same file.
        assert core.get_cycle("nope") is None
    finally:
        core.close()
        learning.close()
