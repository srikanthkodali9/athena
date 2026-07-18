"""reset_demo.sh clears runtime state without touching fixtures/code (TST-06)."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"


def test_scripts_exist_and_are_executable():
    for name in ("start_demo.sh", "reset_demo.sh", "run_replay.sh", "demo_eval.py"):
        p = _SCRIPTS / name
        assert p.exists(), f"missing script {name}"
        mode = p.stat().st_mode
        assert mode & stat.S_IXUSR, f"{name} is not executable"


def test_reset_removes_only_state(tmp_path):
    db = tmp_path / "state" / "athena.db"
    db.parent.mkdir(parents=True)
    db.write_text("sqlite-ish")
    (db.parent / "athena.db-wal").write_text("wal")
    (db.parent / "athena.db-shm").write_text("shm")

    env = dict(os.environ, ATHENA_STATE_DB_PATH=str(db))
    result = subprocess.run(
        ["bash", str(_SCRIPTS / "reset_demo.sh")],
        cwd=str(_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not db.exists()
    assert not (db.parent / "athena.db-wal").exists()
    assert not (db.parent / "athena.db-shm").exists()
    # The directory is preserved so the next run can recreate the store.
    assert db.parent.is_dir()
    # No fixtures were touched.
    assert (_ROOT / "fixtures" / "incidents" / "incident-one.json").exists()
