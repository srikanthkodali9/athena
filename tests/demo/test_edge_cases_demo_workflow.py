"""Separate workflow coverage for the ``fixtures/edge_cases`` catalog."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.ui.edge_cases_demo_runner import _catalog_path, run_edge_cases_demo

ROOT = Path(__file__).resolve().parents[2]


def test_edge_cases_workflow_passes_without_existing_fixture_registry(monkeypatch):
    """The new workflow must never fall through to ``app.tools.fixtures``."""

    def _mixed_fixture_access(*args, **kwargs):
        raise AssertionError("existing fixture registry must not be used")

    monkeypatch.setattr("app.tools.fixtures.get_fixture", _mixed_fixture_access)
    result = run_edge_cases_demo()

    assert result.passed is True
    assert result.fixture_root == "fixtures/edge_cases"
    assert result.isolation == {
        "uses_existing_fixture_registry": False,
        "writes_core_demo_state": False,
        "uses_only_edge_cases_manifest_paths": True,
        "oracle_separated_from_runtime": True,
    }


def test_edge_cases_core_scenarios_revise_then_reuse():
    result = run_edge_cases_demo()
    first, learned = result.scenarios

    assert first.scenario_id == "redis-001"
    assert first.mid_cycle_decision == "REVISE_PLAN"
    assert first.skill_status == "promoted"
    assert first.replay_passes == 3
    assert first.known_false_leads
    assert first.evaluation and first.evaluation.passed

    assert learned.scenario_id == "redis-002"
    assert learned.mid_cycle_decision == "CONTINUE_PLAN"
    assert learned.skill_reused is True
    assert learned.skill_id == "sre.redis.connection-saturation"
    assert learned.evaluation and learned.evaluation.passed

    # The measured gate uses results derived from runtime datasets, not the
    # expected-outcome target values themselves.
    assert result.comparison.passed is True
    assert set(result.comparison.improved_metrics) == {
        "root_cause_confidence",
        "unsupported_hypotheses",
    }


def test_edge_cases_required_evidence_comes_from_runtime_datasets():
    result = run_edge_cases_demo()
    for scenario in result.scenarios:
        oracle_path = (
            ROOT
            / "fixtures"
            / "edge_cases"
            / "incidents"
            / scenario.scenario_id
            / "expected-outcome.json"
        )
        oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
        assert set(oracle["required_evidence_ids"]) <= set(scenario.evidence_ids)
        assert scenario.oracle_used_as_runtime_input is False
        assert scenario.redis_rejection_preceded_service_error is True
        assert scenario.maximum_possible_clients > scenario.redis_maxclients


def test_edge_cases_security_and_all_edge_cases_pass():
    result = run_edge_cases_demo()
    security = result.security
    assert security.passed is True
    assert security.poisoned_runbook_action == "quarantine"
    assert security.poisoned_tool_result_action == "quarantine"
    assert security.poisoned_persistence_action == "reject_persistence"
    assert security.protected_path_decision == "deny"
    assert security.unapproved_egress_decision == "deny"
    assert security.unavailable_gateway_actions == {
        "athena_response": "allow",
        "tool_call": "block",
        "skill_write": "reject_persistence",
    }
    assert len(result.validations) == 8
    assert all(edge.passed for edge in result.validations)


def test_edge_cases_manifest_path_cannot_escape_to_existing_fixtures():
    with pytest.raises(ValueError, match="escapes fixture root"):
        _catalog_path("fixtures/incidents/incident-one.json")


def test_edge_cases_demo_endpoint_does_not_mutate_existing_demo(demo_client):
    before = demo_client.get("/api/v1/demo/state").json()
    assert before["has_run"] is False
    assert before["security"]["inspection_count"] == 0

    response = demo_client.post("/api/v1/demo/edge-cases/run")
    assert response.status_code == 200, response.text
    assert response.json()["passed"] is True

    after = demo_client.get("/api/v1/demo/state").json()
    assert after["has_run"] is False
    assert after["security"]["inspection_count"] == 0
    assert after["edge_cases"]["passed"] is True


def test_dashboard_has_single_run_control_and_edge_cases_view(demo_client):
    """The redesigned console has one run control and surfaces edge cases as its own
    navigable page (its panels are rendered client-side from /demo/console)."""
    response = demo_client.get("/dashboard/")
    assert response.status_code == 200, response.text
    html = response.text

    assert html.count('id="run"') == 1
    assert 'data-page="edge-cases"' in html
    assert 'id="page-edge-cases"' in html


def test_run_cycle_refreshes_dashboard_before_reenabling_button(demo_client):
    response = demo_client.get("/dashboard/app.js")
    assert response.status_code == 200, response.text

    script = response.text
    start = script.index("async function runCycle()")
    end = script.index("\ndocument.querySelectorAll", start)
    handler = script[start:end]

    assert "finally" in handler
    assert handler.index('await fetch(API + "/demo/run"') < handler.index(
        "await refresh()"
    )
    assert handler.index("await refresh()") < handler.index("btn.disabled = false")


def test_edge_cases_demo_script_returns_success_and_json():
    completed = subprocess.run(
        [sys.executable, "scripts/edge_cases_demo.py", "--json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    body = json.loads(completed.stdout)
    assert body["workflow"] == "edge_cases_fixture_workflow"
    assert body["passed"] is True
