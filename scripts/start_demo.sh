#!/usr/bin/env bash
# start_demo.sh — one command to launch the judge-facing Athena demo.
#
# Deterministic MOCK mode by default (no GPU, no external services). Performs a
# clean reset first so every launch starts from zero cycles, then serves the
# demo control plane + dashboard.
#
#   Dashboard:  http://127.0.0.1:8000/dashboard/
#   API base:   http://127.0.0.1:8000/api/v1
#
# Override provider modes for a live run:
#   ATHENA_INFERENCE_MODE=live ATHENA_SECURITY_MODE=live ./scripts/start_demo.sh
set -euo pipefail

cd "$(dirname "$0")/.."

export ATHENA_ENV="${ATHENA_ENV:-demo}"
export ATHENA_INFERENCE_MODE="${ATHENA_INFERENCE_MODE:-mock}"
export ATHENA_SECURITY_MODE="${ATHENA_SECURITY_MODE:-mock}"
export ATHENA_STATE_DB_PATH="${ATHENA_STATE_DB_PATH:-.athena/athena.db}"
HOST="${ATHENA_HOST:-127.0.0.1}"
PORT="${ATHENA_PORT:-8000}"

./scripts/reset_demo.sh

echo "start_demo: env=$ATHENA_ENV inference=$ATHENA_INFERENCE_MODE security=$ATHENA_SECURITY_MODE"
echo "start_demo: dashboard -> http://$HOST:$PORT/dashboard/"
echo "start_demo: press the 'Run demo' button, or run ./scripts/run_replay.sh in another shell."

exec uv run uvicorn "app.ui.server:create_demo_app" --factory --host "$HOST" --port "$PORT"
