"""Contract-shape tests — protects the shared port core_developer imports."""

from __future__ import annotations

from app.security.adapters import LocalHiddenLayerAdapter
from app.security.config import SecurityConfig
from app.security.contracts import (
    FAIL_CLOSED_STAGES,
    PERSISTENT_WRITE_STAGES,
    AgentEffect,
    InspectionRequest,
    InspectionStage,
    SecurityAction,
    SecurityDecision,
    SecurityGateway,
    Severity,
)
from app.security.gateway import HiddenLayerGateway


def test_inspection_stage_covers_all_required_stages():
    expected = {
        "heartbeat_input", "retrieved_content", "user_prompt", "model_prompt",
        "specialist_response", "athena_response", "tool_call", "tool_result",
        "memory_write", "skill_write", "runbook_write",
    }
    assert {s.value for s in InspectionStage} == expected


def test_security_action_set_is_exactly_the_agreed_set():
    assert {a.value for a in SecurityAction} == {
        "allow", "redact", "exclude", "quarantine", "block", "reject_persistence",
    }


def test_decision_exposes_all_coordinator_fields():
    decision = SecurityDecision(
        action=SecurityAction.ALLOW,
        severity=Severity.INFO,
        confidence=0.9,
    )
    for field in (
        "action", "severity", "confidence", "finding", "trust_score_before",
        "trust_score_after", "agent_effect", "security_incident_created",
    ):
        assert hasattr(decision, field)


def test_fail_closed_stages_are_tool_call_and_persistent_writes():
    assert InspectionStage.TOOL_CALL in FAIL_CLOSED_STAGES
    assert PERSISTENT_WRITE_STAGES <= FAIL_CLOSED_STAGES
    assert InspectionStage.RETRIEVED_CONTENT not in FAIL_CLOSED_STAGES


def test_gateway_satisfies_the_port_protocol():
    gw = HiddenLayerGateway(LocalHiddenLayerAdapter(), SecurityConfig(mode="mock"))
    assert isinstance(gw, SecurityGateway)


def test_clean_content_is_allowed_unchanged(gateway):
    req = InspectionRequest(
        stage=InspectionStage.RETRIEVED_CONTENT,
        producer="prometheus",
        consumer="observability_specialist",
        content="p99 latency rose to 800ms on redis-cache between 10:00 and 10:05.",
    )
    decision = gateway.inspect(req)
    assert decision.action is SecurityAction.ALLOW
    assert decision.agent_effect is AgentEffect.USED_IN_CONTEXT
    assert decision.safe_content == req.content
    assert decision.is_usable()


def test_readiness_marks_mock_mode(gateway):
    r = gateway.readiness()
    assert r["mode"] == "mock"
    assert r["is_mock"] is True
