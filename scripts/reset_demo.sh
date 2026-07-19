#!/usr/bin/env bash
# reset_demo.sh — deterministic clean reset of Athena demo state.
#
# Removes ONLY generated runtime state (the SQLite store + WAL/SHM sidecars).
# No fixtures, code, or config are touched, so a fresh run is byte-for-byte
# reproducible with no manual file edits between runs (TST-06).
set -euo pipefail

cd "$(dirname "$0")/.."

DB_PATH="${ATHENA_STATE_DB_PATH:-.athena/athena.db}"
DB_DIR="$(dirname "$DB_PATH")"

removed=0
for f in "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm" "$DB_PATH-journal"; do
  if [ -e "$f" ]; then rm -f "$f"; removed=$((removed+1)); fi
done

mkdir -p "$DB_DIR"

echo "reset_demo: cleared $removed state file(s) under '$DB_DIR' (db=$DB_PATH)."
echo "reset_demo: demo state is clean. Next start/replay begins from zero cycles."
