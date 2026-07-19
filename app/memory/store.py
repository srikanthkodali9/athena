"""Durable SQLite cycle store (FR-7).

A thin, transactional repository behind which the rest of the app depends. It
persists whole ``Cycle`` records as JSON alongside indexed columns, records
per-step ``Checkpoint`` rows transactionally with the owning cycle's state, and
keeps an immutable evidence registry.

Restart recovery (FR7-04/05/07):
- ``find_incomplete_cycle`` reloads a non-terminal cycle after a crash.
- ``completed_steps`` reports which steps already ran so the step engine avoids
  repeating costly/irreversible work.
- ``checkpoint`` writes the step row and updates the cycle in a single
  transaction, so a crash can never leave a step "done" without the matching
  cycle update (or vice versa).

SQLite operations are synchronous and fast for local state; async callers wrap
them with ``asyncio.to_thread`` where they must not block the event loop.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from app.athena.schemas import (
    TERMINAL_STATES,
    Checkpoint,
    Cycle,
    CycleState,
    Evidence,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    cycle_id        TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    state           TEXT NOT NULL,
    trigger         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    data            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id   TEXT NOT NULL,
    step       TEXT NOT NULL,
    status     TEXT NOT NULL,
    costly     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    data       TEXT NOT NULL,
    UNIQUE(cycle_id, step),
    FOREIGN KEY(cycle_id) REFERENCES cycles(cycle_id)
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    cycle_id    TEXT,
    type        TEXT NOT NULL,
    source      TEXT NOT NULL,
    produced_by TEXT,
    created_at  TEXT NOT NULL,
    data        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cycles_state ON cycles(state);
CREATE INDEX IF NOT EXISTS idx_checkpoints_cycle ON checkpoints(cycle_id);
CREATE INDEX IF NOT EXISTS idx_evidence_cycle ON evidence(cycle_id);
"""


class CycleStore:
    """Transactional durable store for cycles, checkpoints, and evidence."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        self._lock = threading.RLock()
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False so a single connection can back the app; the
        # RLock serializes access, which is sufficient for local hackathon use.
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

    # -- Cycles ------------------------------------------------------------

    def save_cycle(self, cycle: Cycle) -> None:
        """Insert or replace a whole cycle record."""
        payload = cycle.model_dump_json()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO cycles
                    (cycle_id, idempotency_key, state, trigger, created_at, updated_at, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cycle_id) DO UPDATE SET
                    idempotency_key=excluded.idempotency_key,
                    state=excluded.state,
                    trigger=excluded.trigger,
                    updated_at=excluded.updated_at,
                    data=excluded.data
                """,
                (
                    cycle.cycle_id,
                    cycle.idempotency_key,
                    cycle.state.value,
                    cycle.trigger.value,
                    cycle.created_at.isoformat(),
                    _now_iso(),
                    payload,
                ),
            )
            self._conn.commit()

    def get_cycle(self, cycle_id: str) -> Optional[Cycle]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM cycles WHERE cycle_id = ?", (cycle_id,)
            ).fetchone()
        return Cycle.model_validate_json(row["data"]) if row else None

    def get_by_idempotency_key(self, key: str) -> Optional[Cycle]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM cycles WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return Cycle.model_validate_json(row["data"]) if row else None

    def active_cycle(self) -> Optional[Cycle]:
        """Any non-terminal cycle (overlap protection, FR1-06)."""
        terminal = tuple(s.value for s in TERMINAL_STATES)
        placeholders = ",".join("?" for _ in terminal)
        with self._lock:
            row = self._conn.execute(
                f"SELECT data FROM cycles WHERE state NOT IN ({placeholders}) "
                "ORDER BY created_at ASC LIMIT 1",
                terminal,
            ).fetchone()
        return Cycle.model_validate_json(row["data"]) if row else None

    def find_incomplete_cycle(self) -> Optional[Cycle]:
        """Reload the oldest non-terminal cycle for restart recovery (FR7-05)."""
        return self.active_cycle()

    def list_cycles(self, limit: int = 50) -> list[Cycle]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM cycles ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Cycle.model_validate_json(r["data"]) for r in rows]

    # -- Checkpoints (transactional with cycle) ----------------------------

    def checkpoint(self, cycle: Cycle, checkpoint: Checkpoint) -> None:
        """Persist a completed step AND the owning cycle in ONE transaction.

        Idempotent on ``(cycle_id, step)``: re-recording a step is a no-op for
        the checkpoint row but still refreshes the cycle snapshot.
        """
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute(
                    """
                    INSERT INTO checkpoints
                        (cycle_id, step, status, costly, created_at, data)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cycle_id, step) DO UPDATE SET
                        status=excluded.status,
                        costly=excluded.costly,
                        data=excluded.data
                    """,
                    (
                        checkpoint.cycle_id,
                        checkpoint.step,
                        checkpoint.status,
                        int(checkpoint.costly),
                        checkpoint.created_at.isoformat(),
                        json.dumps(checkpoint.result),
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO cycles
                        (cycle_id, idempotency_key, state, trigger, created_at, updated_at, data)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cycle_id) DO UPDATE SET
                        idempotency_key=excluded.idempotency_key,
                        state=excluded.state,
                        updated_at=excluded.updated_at,
                        data=excluded.data
                    """,
                    (
                        cycle.cycle_id,
                        cycle.idempotency_key,
                        cycle.state.value,
                        cycle.trigger.value,
                        cycle.created_at.isoformat(),
                        _now_iso(),
                        cycle.model_dump_json(),
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def completed_steps(self, cycle_id: str) -> dict[str, Checkpoint]:
        """Return completed steps for recovery (done vs pending, FR7-05)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT cycle_id, step, status, costly, created_at, data "
                "FROM checkpoints WHERE cycle_id = ? ORDER BY id ASC",
                (cycle_id,),
            ).fetchall()
        out: dict[str, Checkpoint] = {}
        for r in rows:
            out[r["step"]] = Checkpoint(
                cycle_id=r["cycle_id"],
                step=r["step"],
                status=r["status"],
                costly=bool(r["costly"]),
                result=json.loads(r["data"]),
            )
        return out

    def has_step(self, cycle_id: str, step: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM checkpoints WHERE cycle_id = ? AND step = ?",
                (cycle_id, step),
            ).fetchone()
        return row is not None

    # -- Evidence registry (immutable, FR6-01) -----------------------------

    def register_evidence(
        self, evidence: Evidence, cycle_id: Optional[str], produced_by: str
    ) -> None:
        """Immutable insert; re-registering the same id is ignored."""
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO evidence
                    (evidence_id, cycle_id, type, source, produced_by, created_at, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.id,
                    cycle_id,
                    evidence.type,
                    evidence.source,
                    produced_by,
                    evidence.collected_at.isoformat(),
                    evidence.model_dump_json(),
                ),
            )
            self._conn.commit()

    def get_evidence(self, evidence_id: str) -> Optional[Evidence]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM evidence WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
        return Evidence.model_validate_json(row["data"]) if row else None


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
