#!/usr/bin/env bash
# run_replay.sh — drive the full demo against a running server and print a
# judge-readable summary (deterministic in mock mode).
#
# Assumes ./scripts/start_demo.sh is already running. For a byte-for-byte clean
# replay, stop the server, run ./scripts/reset_demo.sh, restart, then replay.
#
#   BASE=http://127.0.0.1:8000 ./scripts/run_replay.sh
set -euo pipefail

cd "$(dirname "$0")/.."

BASE="${BASE:-http://127.0.0.1:8000}"
API="$BASE/api/v1"
PY="$(command -v python3 || echo python)"

pp() { "$PY" -m json.tool 2>/dev/null || cat; }

echo "== run_replay: config/status =="
curl -sf "$API/config/status" | pp

echo; echo "== run_replay: running demo (incident one, incident two, security drill) =="
curl -sf -X POST "$API/demo/run" | pp

echo; echo "== run_replay: first-vs-learned comparison (FR-14, from persisted records) =="
curl -sf "$API/demo/comparison" | pp

echo; echo "== run_replay: security events =="
curl -sf "$API/security/events" | pp

echo; echo "== run_replay: security denials =="
curl -sf "$API/security/denials" | pp

echo; echo "== run_replay: improvement gate =="
BASE="$BASE" uv run python scripts/demo_eval.py --server "$BASE" || {
  echo "run_replay: improvement gate NOT met (expected in pure mock mode until skill-reuse lands)."
}
