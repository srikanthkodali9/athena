"""Fixtures are well-formed, aligned to the tool registry, and secret-free."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.tools import fixtures as tool_fixtures
from app.tools.registry import build_default_registry

_ROOT = Path(__file__).resolve().parents[2]
_FIX = _ROOT / "fixtures"


def _load(rel: str) -> dict:
    return json.loads((_FIX / rel).read_text(encoding="utf-8"))


# Obvious real-secret shapes that must never appear in a fixture. Placeholders
# like AKIA_PLACEHOLDER_EXAMPLE are allowed (they are clearly synthetic).
_FORBIDDEN = ("BEGIN RSA PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY")


def _all_fixture_files():
    return sorted(p for p in _FIX.rglob("*") if p.suffix in {".json", ".md"})


def test_no_real_secret_material():
    for path in _all_fixture_files():
        text = path.read_text(encoding="utf-8")
        for bad in _FORBIDDEN:
            assert bad not in text, f"{path} contains forbidden secret marker {bad!r}"
        # Any real AWS key would be AKIA + 16 uppercase/digits with no underscore.
        import re

        for m in re.finditer(r"AKIA[A-Z0-9]{16}", text):
            assert "PLACEHOLDER" in text[m.start(): m.start() + 40], (
                f"{path} has an AKIA token that is not an obvious placeholder"
            )


@pytest.mark.parametrize("fixture", ["redis-incident-001", "redis-incident-002"])
def test_incident_fixture_shape_and_ground_truth(fixture):
    name = "incident-one.json" if fixture.endswith("001") else "incident-two.json"
    data = _load(f"incidents/{name}")
    assert data["fixture"] == fixture
    assert data["synthetic"] is True
    assert data["contains_real_secret"] is False
    gt = data["ground_truth"]
    assert gt["expected_decision"] == "ESCALATE"
    assert gt["expected_evidence_ids"], "ground truth must list expected evidence IDs"
    # Every expected evidence id must actually be producible by the fixture.
    ev_ids = [m["id"] for m in data["metrics"]] + [data["deployment_diff"]["id"]]
    for eid in gt["expected_evidence_ids"]:
        assert eid in ev_ids, f"{eid} not present in fixture evidence"


def test_incident_one_declares_revise_contract():
    data = _load("incidents/incident-one.json")
    mc = data["mid_cycle"]
    assert mc["expected_decision"] == "REVISE_PLAN"
    assert mc["trigger_evidence_id"] == "ev_metric-redis-conn-20"
    assert mc["original_platform_question"] != mc["revised_platform_question"]


def test_incident_two_declares_skill_reuse():
    data = _load("incidents/incident-two.json")
    reuse = data["reuses_skill"]
    one = _load("incidents/incident-one.json")
    # Incident two must reference the same skill incident one expects to produce.
    assert reuse["skill_id_hint"] == one["expected_skill"]["skill_id_hint"]
    assert reuse["expected_efficiency"]["prior_skill_reused"] is True


def test_tool_registry_produces_expected_evidence_ids():
    """The canonical ground-truth IDs match what the running tools emit."""
    registry = build_default_registry()
    for fixture in ("redis-incident-001", "redis-incident-002"):
        assert tool_fixtures.known_fixture(fixture)
        metrics = registry.call("query_metrics", {"fixture": fixture})
        diff = registry.call("diff_deployment", {"fixture": fixture})
        assert metrics.status == "success" and diff.status == "success"
        produced = set(metrics.evidence_ids) | set(diff.evidence_ids)
        # incident-one ground truth IDs come from the tool registry directly.
        if fixture == "redis-incident-001":
            gt = set(_load("incidents/incident-one.json")["ground_truth"]["expected_evidence_ids"])
            assert gt == produced, f"tool evidence {produced} != ground truth {gt}"


def test_runbooks_present_clean_and_poisoned():
    clean = (_FIX / "runbooks" / "redis_latency_clean.md").read_text(encoding="utf-8")
    poisoned = (_FIX / "runbooks" / "redis_latency_poisoned.md").read_text(encoding="utf-8")
    # The clean runbook must NOT contain the attack instructions.
    assert "ignore previous" not in clean.lower()
    assert "evil-diagnostics" not in clean.lower()
    # The poisoned runbook must contain the §21.3 attack signature.
    assert "ignore previous" in poisoned.lower()
    assert "credential" in poisoned.lower()
