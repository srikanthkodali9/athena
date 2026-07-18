"""OpenShell capability enforcement — independent of HiddenLayer trust (FR-11)."""

from __future__ import annotations

import json
from pathlib import Path

from app.policies.models import PolicyEffect, PolicyRequest
from app.security.events import SecurityLedger
from app.security.openshell import OpenShellEngine

ATTACKS_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "attacks"


# (b) protected credential path denied by OpenShell independently ------------

def test_protected_credential_path_denied(engine):
    fixture = json.loads((ATTACKS_DIR / "credential_access.json").read_text())
    decision = engine.evaluate(PolicyRequest(**fixture["policy_request"]))
    assert decision.effect is PolicyEffect.DENY
    assert decision.category == "protected_path"
    assert decision.resource_category == "aws_credential_path"


def test_additional_credential_paths_denied(engine):
    fixture = json.loads((ATTACKS_DIR / "credential_access.json").read_text())
    for path in fixture["additional_denied_paths"]:
        decision = engine.evaluate(
            PolicyRequest(role="athena", action="read_repository", resource=path)
        )
        assert decision.effect is PolicyEffect.DENY, path


def test_credential_denial_is_independent_of_content_trust(engine):
    # No HiddenLayer object exists in this test at all — proving independence.
    decision = engine.evaluate(
        PolicyRequest(role="observability", action="read_telemetry", resource="~/.aws/credentials")
    )
    assert decision.effect is PolicyEffect.DENY


# (c) unapproved network egress denied independently -------------------------

def test_unapproved_egress_denied(engine):
    fixture = json.loads((ATTACKS_DIR / "unapproved_egress.json").read_text())
    decision = engine.evaluate(PolicyRequest(**fixture["policy_request"]))
    assert decision.effect is PolicyEffect.DENY
    assert decision.category == "network_egress"
    assert decision.resource_category == "unapproved_destination"


def test_approved_egress_allowed(engine):
    fixture = json.loads((ATTACKS_DIR / "unapproved_egress.json").read_text())
    decision = engine.evaluate(PolicyRequest(**fixture["approved_control_request"]))
    assert decision.effect is PolicyEffect.ALLOW


# Positive + approval-gated + least-privilege --------------------------------

def test_approved_diagnostic_is_allowed(engine):
    decision = engine.evaluate(
        PolicyRequest(role="athena", action="run_diagnostic", resource="./app/tools/ping.py")
    )
    assert decision.effect is PolicyEffect.ALLOW


def test_git_patch_requires_approval(engine):
    decision = engine.evaluate(PolicyRequest(role="athena", action="create_git_patch"))
    assert decision.effect is PolicyEffect.REQUIRE_APPROVAL


def test_least_privilege_blocks_ungranted_capability(engine):
    # Observability may not open PRs; critic has no execution capability at all.
    assert engine.evaluate(
        PolicyRequest(role="observability", action="open_draft_pr")
    ).effect is PolicyEffect.DENY
    assert engine.evaluate(
        PolicyRequest(role="critic", action="run_diagnostic")
    ).effect is PolicyEffect.DENY


def test_security_policy_modification_denied(engine):
    for action in ("modify_security_policy", "disable_hiddenlayer", "apply_production_change"):
        assert engine.evaluate(
            PolicyRequest(role="athena", action=action)
        ).effect is PolicyEffect.DENY, action


def test_unknown_capability_denied_by_default(engine):
    assert engine.evaluate(
        PolicyRequest(role="athena", action="format_root_disk")
    ).effect is PolicyEffect.DENY


def test_denials_recorded_in_ledger():
    ledger = SecurityLedger()
    engine = OpenShellEngine.from_path(
        str(Path(__file__).resolve().parents[2] / "policies" / "openshell.yaml"),
        ledger=ledger,
    )
    engine.evaluate(PolicyRequest(role="athena", action="read_repository", resource="~/.aws/credentials"))
    engine.evaluate(
        PolicyRequest(role="athena", action="call_model_endpoint", destination="https://evil.example.com")
    )
    denials = ledger.denials()
    assert len(denials) == 2
    # Sanitized: no raw path/host leaks into the denial record.
    blob = json.dumps([d.to_api_dict() for d in denials])
    assert "evil.example.com" not in blob
    assert ".aws/credentials" not in blob


def test_policy_is_versioned(engine):
    assert engine.policy_version == "1.0.0"
