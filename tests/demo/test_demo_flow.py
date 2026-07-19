"""End-to-end demo flow in deterministic mock mode.

Drives the full demo through the demo control plane, then asserts:
* both incidents are persisted and completed,
* the §21.3 security drill is visible on the real security routes,
* the FR-14 comparison is computed from PERSISTED records (cross-checked
  against /metrics and /heartbeats),
* the run is deterministic across two clean resets.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.ui.server import create_demo_app
from tests.demo.conftest import demo_settings

# Stable (timing-independent) fields that must match across clean reruns.
_STABLE_METRICS = ("model_calls", "tool_calls", "specialists_invoked", "total_tokens")


def _run_demo(client: TestClient) -> dict:
    r = client.post("/api/v1/demo/run")
    assert r.status_code == 200, r.text
    return r.json()


def test_full_demo_runs_and_persists_both_incidents(demo_client):
    summary = _run_demo(demo_client)
    one_id = summary["incident_one_cycle_id"]
    two_id = summary["incident_two_cycle_id"]
    assert one_id.startswith("cycle_") and two_id.startswith("cycle_")

    one = demo_client.get(f"/api/v1/heartbeats/{one_id}").json()
    two = demo_client.get(f"/api/v1/heartbeats/{two_id}").json()
    assert one["state"] == "completed" and two["state"] == "completed"
    assert one["decision"] == "ESCALATE"
    assert len(one["evidence_ids"]) == 3  # incident-one ground truth


def test_shared_dashboard_run_executes_both_fixture_workflows(demo_client):
    summary = _run_demo(demo_client)

    # Existing standard-demo response fields remain top-level for CLI/API
    # compatibility, while the isolated fixture result is deliberately nested.
    assert summary["incident_one_cycle_id"].startswith("cycle_")
    assert summary["incident_two_cycle_id"].startswith("cycle_")
    assert summary["codex"]["fixture_root"] == "fixtures/codex"
    assert summary["codex"]["passed"] is True
    assert len(summary["codex"]["scenarios"]) == 2

    state = demo_client.get("/api/v1/demo/state").json()
    assert state["has_run"] is True
    assert state["codex"]["passed"] is True
    assert len(state["codex"]["edge_cases"]) == 8


def test_autonomy_and_plan_visible_from_persisted_state(demo_client):
    _run_demo(demo_client)
    state = demo_client.get("/api/v1/demo/state").json()
    assert state["has_run"] is True
    c1 = state["incident_one"]
    # First screen proves autonomy: a manual/periodic trigger, no user prompt.
    assert c1["trigger"] in ("manual", "periodic")
    assert c1["change_class"] == "meaningful"
    # The plan + mid-cycle decision are read straight from the persisted cycle.
    assert c1["plan"] is not None
    assert c1["mid_cycle_decision"] in ("CONTINUE_PLAN", "REVISE_PLAN")


def test_security_drill_quarantine_and_denials_visible(demo_client):
    summary = _run_demo(demo_client)

    # The clean runbook is allowed (contrast) — the gateway only ledgers notable
    # events, so read the allow from the drill result the run returns.
    drill = summary["security_drill"]
    clean = next(h for h in drill["hiddenlayer"] if h["check"] == "clean_runbook_retrieved")
    assert clean["action"] == "allow" and clean["usable_downstream"] is True

    events = demo_client.get("/api/v1/security/events").json()["events"]
    # The poisoned runbook is quarantined and its source trust collapses to 0.
    quarantined = [e for e in events if e["action"] == "quarantine"]
    assert quarantined, "poisoned runbook must be quarantined"
    assert any(e["trust_score_after"] == 0.0 for e in quarantined)
    assert any(e["producer"] == "runbook:redis_latency_poisoned" for e in quarantined)
    # Attempting to persist it as a skill is rejected.
    assert any(e["action"] == "reject_persistence" for e in events)

    denials = demo_client.get("/api/v1/security/denials").json()["denials"]
    categories = {d["category"] for d in denials}
    # OpenShell denies credential access AND egress, independently of HiddenLayer.
    assert "protected_path" in categories
    assert "network_egress" in categories


def test_comparison_comes_from_persisted_records(demo_client):
    _run_demo(demo_client)
    cmp = demo_client.get("/api/v1/demo/comparison").json()
    assert cmp["available"] is True
    assert cmp["source"] == "persisted_cycle_records"

    first = cmp["first"]
    cid = first["cycle_id"]
    # Cross-check the snapshot against the same records the metrics/heartbeat
    # routes serve — proves the numbers are not hard-coded in the UI.
    metrics = demo_client.get(f"/api/v1/metrics/cycles/{cid}").json()["metrics"]
    hb = demo_client.get(f"/api/v1/heartbeats/{cid}").json()["metrics"]
    assert first["tool_calls"] == metrics["tool_calls"]
    assert first["specialist_calls"] == hb["specialists_invoked"]
    assert first["main_model_calls"] == metrics["model_calls"] - hb["specialists_invoked"]


def test_gate_met_with_skill_reuse(demo_client):
    """FR-14: once the skill-reuse hook is wired, incident two reuses the
    promoted Redis skill and runs leaner (fewer specialist + main-model calls),
    so >= 2 metrics improve from PERSISTED records — the improvement is real,
    never hard-coded."""
    _run_demo(demo_client)
    cmp = demo_client.get("/api/v1/demo/comparison").json()
    ev = cmp["evaluation"]
    assert ev["passed"] is True
    assert ev["improved_count"] >= 2


def test_demo_is_deterministic_across_clean_resets(tmp_path):
    """Two runs from clean state produce identical stable metrics (TST-06)."""

    def run(subdir) -> dict:
        settings = demo_settings(tmp_path / subdir)
        app = create_demo_app(settings=settings)
        with TestClient(app) as client:
            summary = _run_demo(client)
            one = client.get(
                f"/api/v1/heartbeats/{summary['incident_one_cycle_id']}"
            ).json()
            two = client.get(
                f"/api/v1/heartbeats/{summary['incident_two_cycle_id']}"
            ).json()
            return {"one": one, "two": two}

    a = run("run_a")
    b = run("run_b")
    for incident in ("one", "two"):
        assert a[incident]["evidence_ids"] == b[incident]["evidence_ids"]
        assert a[incident]["decision"] == b[incident]["decision"]
        assert a[incident]["mid_cycle_decision"] == b[incident]["mid_cycle_decision"]
        for key in _STABLE_METRICS:
            assert a[incident]["metrics"][key] == b[incident]["metrics"][key], (
                f"{incident}.{key} not deterministic"
            )
