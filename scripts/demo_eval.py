#!/usr/bin/env python3
"""FR14-08 demo evaluation gate.

Computes the first-vs-learned comparison from PERSISTED cycle records and exits
non-zero when fewer than two metrics improve. Two modes:

* ``--server URL``  hit a running demo server (reads via the API).
* (default)         run everything in-process with a TestClient in mock mode.

Never fabricates improvement — the gate reflects the real persisted deltas.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _print_table(cmp: dict[str, Any]) -> None:
    rows = cmp.get("rows", [])
    print(f"{'metric':<26}{'first':>14}{'learned':>14}{'delta':>12}  result")
    print("-" * 80)
    for r in rows:
        res = "improved" if r.get("improved") is True else (
            "worse" if r.get("improved") is False and r.get("delta") not in (0, 0.0, None, False) else "="
        )
        print(f"{r['label']:<26}{str(r['first']):>14}{str(r['learned']):>14}{str(r['delta']):>12}  {res}")
    ev = cmp.get("evaluation", {})
    print("-" * 80)
    print(f"source: {cmp.get('source')}")
    print(f"gate: {'PASS' if ev.get('passed') else 'NOT MET'} — {ev.get('reason')}")


def _via_server(url: str, min_improved: int) -> dict[str, Any]:
    import httpx

    base = url.rstrip("/") + "/api/v1"
    with httpx.Client(timeout=30.0) as c:
        # Ensure both incidents are persisted, then read the comparison.
        c.post(f"{base}/demo/run", params={"min_improved": min_improved})
        r = c.get(f"{base}/demo/comparison", params={"min_improved": min_improved})
        r.raise_for_status()
        return r.json()


def _in_process(min_improved: int) -> dict[str, Any]:
    import tempfile

    from fastapi.testclient import TestClient

    from app.athena.config import AthenaEnv, Settings
    from app.ui.server import create_demo_app

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            _env_file=None,
            env=AthenaEnv.demo,
            state_db_path=f"{tmp}/athena.db",
            inference_mode="mock",
            security_mode="mock",
        )
        app = create_demo_app(settings=settings)
        with TestClient(app) as client:
            client.post("/api/v1/demo/run", params={"min_improved": min_improved})
            r = client.get("/api/v1/demo/comparison", params={"min_improved": min_improved})
            r.raise_for_status()
            return r.json()


def main() -> int:
    ap = argparse.ArgumentParser(description="Athena FR-14 improvement gate.")
    ap.add_argument("--server", default=None, help="URL of a running demo server.")
    ap.add_argument("--min-improved", type=int, default=2)
    ap.add_argument("--json", action="store_true", help="Emit raw JSON only.")
    args = ap.parse_args()

    cmp = _via_server(args.server, args.min_improved) if args.server else _in_process(args.min_improved)

    if not cmp.get("available", True):
        print(f"comparison unavailable: {cmp.get('reason')}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(cmp, indent=2))
    else:
        _print_table(cmp)

    return 0 if cmp.get("evaluation", {}).get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
