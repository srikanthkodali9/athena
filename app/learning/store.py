"""Learning repository over the shared SQLite file (ADR-0002).

Owns the ``episodes``, ``skills``, ``runbook_proposals``, and ``comparisons``
tables. It opens the SAME database file as core's ``app/memory/store.py``
(``settings.state_db_path``) through its OWN module — it never edits or imports
core's store — so learning writes and cycle writes live in one file with one
reset (ADR-0002 §6) while keeping ownership boundaries clean.

Design notes:

* ``*_json`` columns hold validated Pydantic payloads; indexed scalar columns
  exist for the queries retrieval/promotion need. The whole record is always
  stored in ``data`` so reads reconstruct the exact model.
* Skills are versioned: the primary key is ``(skill_id, version)``.
* No hard foreign keys to core-owned tables (``incidents``/``cycles``): the
  learning pipeline must be fully testable offline in mock mode, decoupled from
  whichever agent seeds those tables. Referential integrity is enforced in the
  service layer, not the schema.
* ``PRAGMA journal_mode=WAL`` + ``foreign_keys=ON`` match ADR-0002 §1.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.learning.schemas import (
    Comparison,
    Episode,
    RunbookProposal,
    Skill,
    SkillStatus,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    episode_id           TEXT PRIMARY KEY,
    incident_id          TEXT,
    cycle_id             TEXT NOT NULL,
    outcome              TEXT,
    verified             INTEGER NOT NULL DEFAULT 0,
    compressed_json      TEXT NOT NULL,
    known_false_leads_json TEXT NOT NULL DEFAULT '[]',
    created_at           TEXT NOT NULL,
    data                 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skills (
    skill_id             TEXT NOT NULL,
    version              TEXT NOT NULL,
    status               TEXT NOT NULL,
    domain               TEXT,
    triggers_json        TEXT NOT NULL DEFAULT '[]',
    preconditions_json   TEXT NOT NULL DEFAULT '[]',
    procedure            TEXT,
    risk                 TEXT,
    requires_approval    INTEGER NOT NULL DEFAULT 0,
    provenance_json      TEXT NOT NULL DEFAULT '{}',
    hiddenlayer_status   TEXT,
    validation_passes    INTEGER NOT NULL DEFAULT 0,
    confidence           REAL NOT NULL DEFAULT 0,
    known_false_leads_json TEXT NOT NULL DEFAULT '[]',
    expires_at           TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    data                 TEXT NOT NULL,
    PRIMARY KEY (skill_id, version)
);

CREATE TABLE IF NOT EXISTS runbook_proposals (
    proposal_id          TEXT PRIMARY KEY,
    source_ref           TEXT NOT NULL,
    diff                 TEXT NOT NULL,
    provenance_json      TEXT NOT NULL DEFAULT '{}',
    validation_status    TEXT,
    hiddenlayer_status   TEXT,
    risk                 TEXT,
    required_reviewer    TEXT,
    created_at           TEXT NOT NULL,
    data                 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comparisons (
    comparison_id        TEXT PRIMARY KEY,
    first_incident_id    TEXT NOT NULL,
    learned_incident_id  TEXT NOT NULL,
    deltas_json          TEXT NOT NULL DEFAULT '{}',
    created_at           TEXT NOT NULL,
    data                 TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_episodes_cycle ON episodes(cycle_id);
CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);
CREATE INDEX IF NOT EXISTS idx_skills_id ON skills(skill_id);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LearningStore:
    """Transactional repository for the learning-plane tables."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        self._lock = threading.RLock()
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- Episodes ----------------------------------------------------------

    def save_episode(self, episode: Episode) -> None:
        import json

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO episodes
                    (episode_id, incident_id, cycle_id, outcome, verified,
                     compressed_json, known_false_leads_json, created_at, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(episode_id) DO UPDATE SET
                    incident_id=excluded.incident_id,
                    outcome=excluded.outcome,
                    verified=excluded.verified,
                    compressed_json=excluded.compressed_json,
                    known_false_leads_json=excluded.known_false_leads_json,
                    data=excluded.data
                """,
                (
                    episode.episode_id,
                    episode.incident_id,
                    episode.cycle_id,
                    episode.outcome,
                    int(episode.verified),
                    json.dumps(episode.compressed),
                    json.dumps([k.model_dump(mode="json") for k in episode.known_false_leads]),
                    episode.created_at.isoformat(),
                    episode.model_dump_json(),
                ),
            )
            self._conn.commit()

    def get_episode(self, episode_id: str) -> Optional[Episode]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM episodes WHERE episode_id = ?", (episode_id,)
            ).fetchone()
        return Episode.model_validate_json(row["data"]) if row else None

    def list_episodes(self, *, verified_only: bool = False) -> list[Episode]:
        sql = "SELECT data FROM episodes"
        if verified_only:
            sql += " WHERE verified = 1"
        sql += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [Episode.model_validate_json(r["data"]) for r in rows]

    # -- Skills (versioned) ------------------------------------------------

    def save_skill(self, skill: Skill) -> None:
        import json

        skill.updated_at = datetime.now(timezone.utc)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO skills
                    (skill_id, version, status, domain, triggers_json,
                     preconditions_json, procedure, risk, requires_approval,
                     provenance_json, hiddenlayer_status, validation_passes,
                     confidence, known_false_leads_json, expires_at,
                     created_at, updated_at, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(skill_id, version) DO UPDATE SET
                    status=excluded.status,
                    domain=excluded.domain,
                    triggers_json=excluded.triggers_json,
                    preconditions_json=excluded.preconditions_json,
                    procedure=excluded.procedure,
                    risk=excluded.risk,
                    requires_approval=excluded.requires_approval,
                    provenance_json=excluded.provenance_json,
                    hiddenlayer_status=excluded.hiddenlayer_status,
                    validation_passes=excluded.validation_passes,
                    confidence=excluded.confidence,
                    known_false_leads_json=excluded.known_false_leads_json,
                    expires_at=excluded.expires_at,
                    updated_at=excluded.updated_at,
                    data=excluded.data
                """,
                (
                    skill.skill_id,
                    skill.version,
                    skill.status.value,
                    skill.domain,
                    json.dumps(skill.triggers),
                    json.dumps(skill.preconditions),
                    "\n".join(skill.procedure),
                    skill.risk.value,
                    int(skill.requires_approval),
                    skill.provenance.model_dump_json(),
                    skill.hiddenlayer_status,
                    skill.validation_passes,
                    skill.confidence,
                    json.dumps([k.model_dump(mode="json") for k in skill.known_false_leads]),
                    skill.expires_at.isoformat() if skill.expires_at else None,
                    skill.created_at.isoformat(),
                    skill.updated_at.isoformat(),
                    skill.model_dump_json(),
                ),
            )
            self._conn.commit()

    def get_skill(self, skill_id: str, version: str) -> Optional[Skill]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM skills WHERE skill_id = ? AND version = ?",
                (skill_id, version),
            ).fetchone()
        return Skill.model_validate_json(row["data"]) if row else None

    def get_skill_versions(self, skill_id: str) -> list[Skill]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM skills WHERE skill_id = ? ORDER BY created_at ASC",
                (skill_id,),
            ).fetchall()
        return [Skill.model_validate_json(r["data"]) for r in rows]

    def latest_version(self, skill_id: str) -> Optional[str]:
        versions = [s.version for s in self.get_skill_versions(skill_id)]
        return versions[-1] if versions else None

    def list_skills(self, *, status: Optional[SkillStatus] = None) -> list[Skill]:
        if status is not None:
            sql = "SELECT data FROM skills WHERE status = ? ORDER BY created_at DESC"
            args: tuple = (status.value,)
        else:
            sql = "SELECT data FROM skills ORDER BY created_at DESC"
            args = ()
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [Skill.model_validate_json(r["data"]) for r in rows]

    # -- Runbook proposals -------------------------------------------------

    def save_runbook_proposal(self, proposal: RunbookProposal) -> None:
        import json

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO runbook_proposals
                    (proposal_id, source_ref, diff, provenance_json,
                     validation_status, hiddenlayer_status, risk,
                     required_reviewer, created_at, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    diff=excluded.diff,
                    provenance_json=excluded.provenance_json,
                    validation_status=excluded.validation_status,
                    hiddenlayer_status=excluded.hiddenlayer_status,
                    risk=excluded.risk,
                    required_reviewer=excluded.required_reviewer,
                    data=excluded.data
                """,
                (
                    proposal.proposal_id,
                    proposal.source_ref,
                    proposal.diff,
                    json.dumps(proposal.provenance),
                    proposal.validation_status.value,
                    proposal.hiddenlayer_status,
                    proposal.risk.value,
                    proposal.required_reviewer,
                    proposal.created_at.isoformat(),
                    proposal.model_dump_json(),
                ),
            )
            self._conn.commit()

    def get_runbook_proposal(self, proposal_id: str) -> Optional[RunbookProposal]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM runbook_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        return RunbookProposal.model_validate_json(row["data"]) if row else None

    def list_runbook_proposals(self) -> list[RunbookProposal]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM runbook_proposals ORDER BY created_at DESC"
            ).fetchall()
        return [RunbookProposal.model_validate_json(r["data"]) for r in rows]

    # -- Comparisons -------------------------------------------------------

    def save_comparison(self, comparison: Comparison) -> None:
        import json

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO comparisons
                    (comparison_id, first_incident_id, learned_incident_id,
                     deltas_json, created_at, data)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(comparison_id) DO UPDATE SET
                    deltas_json=excluded.deltas_json,
                    data=excluded.data
                """,
                (
                    comparison.comparison_id,
                    comparison.first_incident_id,
                    comparison.learned_incident_id,
                    json.dumps(comparison.deltas),
                    comparison.created_at.isoformat(),
                    comparison.model_dump_json(),
                ),
            )
            self._conn.commit()

    def get_comparison(self, comparison_id: str) -> Optional[Comparison]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM comparisons WHERE comparison_id = ?",
                (comparison_id,),
            ).fetchone()
        return Comparison.model_validate_json(row["data"]) if row else None
