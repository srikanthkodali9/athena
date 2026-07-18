from __future__ import annotations

import json
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load_json(relative_path: str) -> dict:
    with (ROOT / relative_path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


class DemoFixtureDatasetTests(unittest.TestCase):
    def test_repository_validator_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/validate_fixtures.py"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Fixture validation passed", result.stdout)

    def test_manifest_references_two_core_scenarios(self) -> None:
        manifest = load_json("fixtures/manifest.json")
        scenario_ids = [item["scenario_id"] for item in manifest["core_scenarios"]]
        self.assertEqual(scenario_ids, ["redis-001", "redis-002"])
        for scenario in manifest["core_scenarios"]:
            self.assertTrue((ROOT / scenario["incident"]).is_file())
            self.assertTrue((ROOT / scenario["expected_outcome"]).is_file())
            for dataset in scenario["datasets"]:
                self.assertTrue((ROOT / dataset).is_file(), dataset)

    def test_unknown_incident_forces_one_plan_revision(self) -> None:
        incident = load_json("fixtures/incidents/redis-001/incident.json")
        expected = load_json("fixtures/incidents/redis-001/expected-outcome.json")
        self.assertEqual(expected["mid_cycle_expectation"]["decision"], "REVISE_PLAN")
        self.assertEqual(incident["investigation"]["mid_cycle"]["maximum_revisions"], 1)
        self.assertNotEqual(
            expected["mid_cycle_expectation"]["original_platform_question"],
            expected["mid_cycle_expectation"]["revised_platform_question"],
        )
        self.assertTrue(expected["learning_expectations"]["known_false_leads"])

    def test_learned_incident_reuses_skill_and_improves_targets(self) -> None:
        first = load_json("fixtures/incidents/redis-001/expected-outcome.json")
        learned = load_json("fixtures/incidents/redis-002/expected-outcome.json")
        incident = load_json("fixtures/incidents/redis-002/incident.json")

        self.assertTrue(learned["learning_expectations"]["reuse_promoted_skill"])
        self.assertEqual(
            incident["retrieved_knowledge"]["skill_id"],
            learned["learning_expectations"]["skill_id"],
        )

        comparisons = [
            learned["measurement_targets"]["investigation_time_seconds_max"]
            < first["measurement_targets"]["investigation_time_seconds_max"],
            learned["measurement_targets"]["tool_calls_max"]
            < first["measurement_targets"]["tool_calls_max"],
            learned["ground_truth"]["minimum_confidence"]
            > first["ground_truth"]["minimum_confidence"],
            learned["measurement_targets"]["unsupported_hypotheses_max"]
            < first["measurement_targets"]["unsupported_hypotheses_max"],
        ]
        self.assertGreaterEqual(sum(comparisons), 2)

    def test_security_fixture_uses_synthetic_non_routable_targets(self) -> None:
        poisoned_runbook = (
            ROOT / "fixtures/runbooks/redis-saturation-poisoned.md"
        ).read_text(encoding="utf-8")
        poisoned_result = load_json("fixtures/attacks/poisoned-tool-result.json")

        self.assertIn("/demo/protected/credentials", poisoned_runbook)
        self.assertIn("example.invalid", poisoned_runbook)
        self.assertEqual(poisoned_result["expected"]["policy_action"], "quarantine")
        self.assertFalse(poisoned_result["expected"]["persistent_memory_allowed"])

        denials = load_json("fixtures/attacks/openshell-denials.json")
        categories = {attempt["category"] for attempt in denials["attempts"]}
        self.assertEqual(categories, {"filesystem_read", "network_egress"})
        self.assertTrue(
            all(attempt["expected_decision"] == "DENY" for attempt in denials["attempts"])
        )

    def test_edge_cases_cover_wait_escalation_revision_and_revalidation(self) -> None:
        no_change = load_json("fixtures/incidents/edge-cases/no-change.json")
        missing = load_json("fixtures/incidents/edge-cases/missing-telemetry.json")
        contradiction = load_json("fixtures/incidents/edge-cases/contradictory-evidence.json")
        stale = load_json("fixtures/incidents/edge-cases/stale-evidence.json")

        self.assertEqual(no_change["expected"]["decision"], "WAIT")
        self.assertEqual(no_change["expected"]["model_calls"], 0)
        self.assertEqual(missing["expected"]["decision"], "ESCALATE")
        self.assertFalse(missing["expected"]["create_learning_artifact"])
        self.assertEqual(contradiction["expected"]["mid_cycle_decision"], "REVISE_PLAN")
        self.assertEqual(stale["expected"]["action"], "REVALIDATE")

    def test_test_oracles_are_not_runtime_datasets(self) -> None:
        manifest = load_json("fixtures/manifest.json")
        for scenario in manifest["core_scenarios"]:
            expected = load_json(scenario["expected_outcome"])
            self.assertTrue(expected["test_oracle_only"])
            self.assertNotIn(scenario["expected_outcome"], scenario["datasets"])

    def test_redis_rejections_precede_service_errors(self) -> None:
        checks = [
            (
                "fixtures/telemetry/redis-001/metrics.json",
                "metric_redis_rejected_001",
                "metric_checkout_5xx_001",
                0.01,
            ),
            (
                "fixtures/telemetry/redis-002/metrics.json",
                "metric_redis_rejected_002",
                "metric_recommendations_5xx_002",
                0.006,
            ),
        ]
        for path, rejection_id, error_id, baseline in checks:
            metrics = load_json(path)
            series = {item["evidence_id"]: item for item in metrics["series"]}
            first_rejection = next(
                point["timestamp"]
                for point in series[rejection_id]["points"]
                if point["value"] > 0
            )
            first_error = next(
                point["timestamp"]
                for point in series[error_id]["points"]
                if point["value"] > baseline
            )
            self.assertLess(
                datetime.fromisoformat(first_rejection.replace("Z", "+00:00")),
                datetime.fromisoformat(first_error.replace("Z", "+00:00")),
            )


if __name__ == "__main__":
    unittest.main()
