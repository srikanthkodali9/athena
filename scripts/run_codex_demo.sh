#!/usr/bin/env bash
# Run the isolated fixtures/codex workflow. This does not start or mutate the
# existing fixtures/* heartbeat demo or its SQLite state.
set -euo pipefail

cd "$(dirname "$0")/.."

uv run python scripts/codex_demo.py "$@"

