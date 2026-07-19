"""Runbook proposals: reviewable, provenance-carrying, source never mutated."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.learning.runbooks import RunbookProposalBuilder
from app.learning.schemas import ProposalValidationStatus, RiskLevel
from app.learning.service import LearningService

from tests.learning.conftest import (
    AllowGateway,
    QuarantineGateway,
    make_redis_cycle,
    redis_evidence,
)

SOURCE_RUNBOOK = """# Redis Incident Runbook

## Steps
1. Check API dashboards.
2. Page the on-call engineer.
"""


def _promoted_skill_and_episode(service, store):
    result = service.learn_from_cycle(make_redis_cycle(), verified=True, evidence=redis_evidence())
    skill = store.get_skill(result.skill_id, result.skill_version)
    episode = store.get_episode(result.episode_id)
    return skill, episode


def test_proposal_generated_with_provenance(service, store, allow_gateway, tmp_path):
    skill, episode = _promoted_skill_and_episode(service, store)
    source = tmp_path / "runbook.md"
    source.write_text(SOURCE_RUNBOOK, encoding="utf-8")

    builder = RunbookProposalBuilder(
        store=store, security=allow_gateway, proposals_dir=tmp_path / "proposals"
    )
    proposal, action = builder.propose_added_steps(
        source_path=source, skill=skill, episode=episode, risk=RiskLevel.MEDIUM,
    )
    assert proposal is not None
    assert proposal.diff  # a reviewable unified diff
    assert proposal.provenance["skill_id"] == skill.skill_id
    assert proposal.provenance["episode_id"] == episode.episode_id
    assert proposal.required_reviewer
    assert proposal.hiddenlayer_status == "passed"
    assert proposal.validation_status is ProposalValidationStatus.VALIDATED
    # Indexed + file written.
    assert store.get_runbook_proposal(proposal.proposal_id) is not None
    assert (tmp_path / "proposals" / f"{proposal.proposal_id}.diff").exists()


def test_source_runbook_byte_unchanged(service, store, allow_gateway, tmp_path):
    skill, episode = _promoted_skill_and_episode(service, store)
    source = tmp_path / "runbook.md"
    source.write_bytes(SOURCE_RUNBOOK.encode("utf-8"))
    before = source.read_bytes()
    before_hash = hashlib.sha256(before).hexdigest()

    builder = RunbookProposalBuilder(
        store=store, security=allow_gateway, proposals_dir=tmp_path / "proposals"
    )
    builder.propose_added_steps(source_path=source, skill=skill, episode=episode)

    after = source.read_bytes()
    assert after == before
    assert hashlib.sha256(after).hexdigest() == before_hash


def test_quarantine_rejects_proposal_persistence(service, store, tmp_path):
    skill, episode = _promoted_skill_and_episode(service, store)
    source = tmp_path / "runbook.md"
    source.write_text(SOURCE_RUNBOOK, encoding="utf-8")
    before = source.read_bytes()

    builder = RunbookProposalBuilder(
        store=store, security=QuarantineGateway(), proposals_dir=tmp_path / "proposals"
    )
    proposal, action = builder.propose_added_steps(
        source_path=source, skill=skill, episode=episode
    )
    assert proposal is None  # fail closed: nothing persisted
    assert store.list_runbook_proposals() == []
    assert source.read_bytes() == before  # source still untouched


def test_known_false_leads_flow_into_proposal_steps(service, store, allow_gateway, tmp_path):
    skill, episode = _promoted_skill_and_episode(service, store)
    source = tmp_path / "runbook.md"
    source.write_text(SOURCE_RUNBOOK, encoding="utf-8")
    builder = RunbookProposalBuilder(
        store=store, security=allow_gateway, proposals_dir=tmp_path / "proposals"
    )
    proposal, _ = builder.propose_added_steps(source_path=source, skill=skill, episode=episode)
    assert "Do NOT" in proposal.diff  # negative knowledge surfaced in the patch
