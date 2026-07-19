#!/usr/bin/env python3
"""Run the isolated ``fixtures/edge_cases`` workflow demo."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Support direct ``python scripts/edge_cases_demo.py`` as well as ``uv run``.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ui.edge_cases_demo_runner import EdgeCasesDemoResult, run_edge_cases_demo  # noqa: E402


def _print_summary(result: EdgeCasesDemoResult) -> None:
    print(f"Edge cases fixture workflow: {'PASS' if result.passed else 'FAIL'}")
    print(f"catalog: {result.catalog_id} ({result.fixture_root})")
    for scenario in result.scenarios:
        state = "PASS" if scenario.evaluation and scenario.evaluation.passed else "FAIL"
        print(
            f"- {scenario.scenario_id}: {state}; "
            f"mid-cycle={scenario.mid_cycle_decision}; "
            f"confidence={scenario.metrics.root_cause_confidence:.2f}; "
            f"skill_reused={scenario.skill_reused}"
        )
        if scenario.evaluation and scenario.evaluation.failures:
            print(f"  failures: {', '.join(scenario.evaluation.failures)}")
    print(
        f"- comparison: {'PASS' if result.comparison.passed else 'FAIL'}; "
        f"{result.comparison.improved_count}/"
        f"{result.comparison.required_improvement_count} required metrics improved"
    )
    print(f"- security: {'PASS' if result.security.passed else 'FAIL'}")
    edge_passes = sum(1 for item in result.validations if item.passed)
    print(f"- edge cases: {edge_passes}/{len(result.validations)} passed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the separate Athena fixtures/edge_cases workflow demo."
    )
    parser.add_argument("--json", action="store_true", help="Print the complete JSON result.")
    args = parser.parse_args()

    result = run_edge_cases_demo()
    if args.json:
        print(json.dumps(result.model_dump(mode="json"), indent=2))
    else:
        _print_summary(result)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
