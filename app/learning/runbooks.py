"""Runbook proposal generation (FR-9) — never mutates a source runbook.

A source runbook is human-owned, read-only, and UNTRUSTED (FR9-01). Athena may
only ever emit a *separate* reviewable patch that carries provenance, validation
status, HiddenLayer result, risk, and a required reviewer (PRD §13.5). The source
file is opened read-only, hashed, and left byte-for-byte unchanged; the proposal
is written under ``runbooks/proposals/`` and indexed in the ``runbook_proposals``
table.

Persistence is security-gated exactly like skills: the proposal content is
inspected at ``RUNBOOK_WRITE`` before it is stored, and a quarantine/block
verdict rejects persistence (fail closed).
"""

from __future__ import annotations

import hashlib
import uuid
from difflib import unified_diff
from pathlib import Path
from typing import Optional

from app.learning.schemas import (
    Episode,
    ProposalValidationStatus,
    RiskLevel,
    RunbookProposal,
    Skill,
)
from app.learning.store import LearningStore
from app.security.contracts import (
    InspectionRequest,
    InspectionStage,
    SecurityAction,
    SecurityGateway,
)


def new_proposal_id() -> str:
    return "rbp_" + uuid.uuid4().hex[:12]


def _hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class RunbookProposalBuilder:
    """Builds reviewable runbook patches without mutating the source."""

    def __init__(
        self,
        *,
        store: LearningStore,
        security: SecurityGateway,
        proposals_dir: Path,
    ) -> None:
        self._store = store
        self._security = security
        self._proposals_dir = Path(proposals_dir)

    def propose_added_steps(
        self,
        *,
        source_path: Path,
        skill: Skill,
        episode: Episode,
        added_steps: Optional[list[str]] = None,
        risk: RiskLevel = RiskLevel.MEDIUM,
        required_reviewer: str = "platform_owner",
        cycle_id: Optional[str] = None,
    ) -> tuple[Optional[RunbookProposal], SecurityAction]:
        """Draft a proposal that appends validated diagnostic steps.

        Returns ``(proposal | None, action)``. ``None`` means the security
        gateway rejected persistence (quarantine/block) — nothing is written.
        The source file is never modified.
        """
        source_path = Path(source_path)
        original = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
        source_hash = _hash_text(original)

        steps = added_steps or self._derive_steps(skill)
        proposed = self._apply_preview(original, skill, steps)
        diff = "".join(
            unified_diff(
                original.splitlines(keepends=True),
                proposed.splitlines(keepends=True),
                fromfile=f"a/{source_path.name}",
                tofile=f"b/{source_path.name}",
            )
        )

        provenance = {
            "skill_id": skill.skill_id,
            "skill_version": skill.version,
            "episode_id": episode.episode_id,
            "created_from_incidents": list(skill.provenance.created_from_incidents),
            "supporting_evidence_ids": list(skill.provenance.supporting_evidence_ids),
        }

        proposal = RunbookProposal(
            proposal_id=new_proposal_id(),
            source_ref=str(source_path),
            source_hash=source_hash,
            kind="add_step",
            title=f"Add validated diagnostic steps from {skill.skill_id}",
            diff=diff,
            provenance=provenance,
            validation_status=(
                ProposalValidationStatus.VALIDATED
                if skill.validation_passes > 0
                else ProposalValidationStatus.DRAFT
            ),
            hiddenlayer_status="pending",
            risk=risk,
            required_reviewer=required_reviewer,
        )

        # Security gate BEFORE persistence (RUNBOOK_WRITE, fail closed).
        decision = self._security.inspect(
            InspectionRequest(
                stage=InspectionStage.RUNBOOK_WRITE,
                producer="learning.runbooks",
                consumer="runbook_proposal_store",
                content=proposal.diff,
                cycle_id=cycle_id,
                metadata={"proposal_id": proposal.proposal_id},
            )
        )
        proposal.security_event_id = decision.security_event_id
        if decision.action in (SecurityAction.ALLOW, SecurityAction.REDACT):
            proposal.hiddenlayer_status = "passed"
        else:
            proposal.hiddenlayer_status = "quarantined"
            # Fail closed: do not persist a rejected proposal, do not mutate source.
            self._assert_source_unchanged(source_path, source_hash)
            return None, decision.action

        self._store.save_runbook_proposal(proposal)
        self._write_proposal_file(proposal)
        # Defensive: prove the source was untouched by the whole operation.
        self._assert_source_unchanged(source_path, source_hash)
        return proposal, decision.action

    # -- Helpers -----------------------------------------------------------

    def _derive_steps(self, skill: Skill) -> list[str]:
        steps = list(skill.procedure[:3])
        for lead in skill.known_false_leads:
            steps.append(f"Do NOT {lead.approach.lower()} — {lead.failure_reason}")
        return steps or ["Follow the validated skill procedure."]

    def _apply_preview(self, original: str, skill: Skill, steps: list[str]) -> str:
        block = [
            "",
            f"## Proposed by Athena ({skill.skill_id} v{skill.version}) — REVIEW REQUIRED",
            "",
        ]
        block += [f"- {s}" for s in steps]
        block.append("")
        base = original if original.endswith("\n") or not original else original + "\n"
        return base + "\n".join(block) + "\n"

    def _write_proposal_file(self, proposal: RunbookProposal) -> Path:
        self._proposals_dir.mkdir(parents=True, exist_ok=True)
        path = self._proposals_dir / f"{proposal.proposal_id}.diff"
        header = [
            f"# proposal_id: {proposal.proposal_id}",
            f"# source_ref: {proposal.source_ref}",
            f"# source_hash: {proposal.source_hash}",
            f"# risk: {proposal.risk.value}",
            f"# required_reviewer: {proposal.required_reviewer}",
            f"# validation_status: {proposal.validation_status.value}",
            f"# hiddenlayer_status: {proposal.hiddenlayer_status}",
            "",
        ]
        path.write_text("\n".join(header) + proposal.diff, encoding="utf-8")
        return path

    def _assert_source_unchanged(self, source_path: Path, expected_hash: str) -> None:
        if not source_path.exists():
            return
        current = _hash_text(source_path.read_text(encoding="utf-8"))
        if current != expected_hash:  # pragma: no cover - safety invariant
            raise RuntimeError(
                f"source runbook {source_path} was modified during proposal generation"
            )
