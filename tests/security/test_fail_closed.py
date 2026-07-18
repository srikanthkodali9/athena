"""Fail-closed vs restricted-mode behaviour when inspection is unavailable."""

from __future__ import annotations

from app.security.contracts import (
    AgentEffect,
    FindingType,
    InspectionRequest,
    InspectionStage,
    SecurityAction,
)


# (d) fail closed on tool_call / persistent write when inspection unavailable -

def test_tool_call_fails_closed_when_inspection_unavailable(unavailable_gateway):
    decision = unavailable_gateway.inspect(
        InspectionRequest(
            stage=InspectionStage.TOOL_CALL,
            producer="router",
            consumer="tool_executor",
            content="query prometheus for redis latency",
            cycle_id="heartbeat-021",
        )
    )
    assert decision.action is SecurityAction.BLOCK
    assert decision.agent_effect is AgentEffect.BLOCKED
    assert decision.safe_content is None
    assert decision.security_incident_created is True
    assert decision.finding is FindingType.INSPECTION_UNAVAILABLE


def test_persistent_writes_fail_closed_when_inspection_unavailable(unavailable_gateway):
    for stage in (
        InspectionStage.MEMORY_WRITE,
        InspectionStage.SKILL_WRITE,
        InspectionStage.RUNBOOK_WRITE,
    ):
        decision = unavailable_gateway.inspect(
            InspectionRequest(
                stage=stage,
                producer="athena",
                consumer="memory_store",
                content="candidate skill body",
                cycle_id="heartbeat-021",
            )
        )
        assert decision.action is SecurityAction.REJECT_PERSISTENCE, stage
        assert decision.agent_effect is AgentEffect.PERSISTENCE_REJECTED
        assert decision.security_incident_created is True


def test_actionable_flag_forces_fail_closed_even_for_summary_stage(unavailable_gateway):
    decision = unavailable_gateway.inspect(
        InspectionRequest(
            stage=InspectionStage.ATHENA_RESPONSE,
            producer="athena",
            consumer="operator",
            content="apply this remediation now",
            metadata={"actionable": True},
        )
    )
    assert decision.action is SecurityAction.BLOCK


# (e) restricted-mode branch for explicitly non-actionable summary -----------

def test_non_actionable_summary_continues_in_restricted_mode(unavailable_gateway):
    decision = unavailable_gateway.inspect(
        InspectionRequest(
            stage=InspectionStage.ATHENA_RESPONSE,
            producer="athena",
            consumer="operator",
            content="Investigation summary: redis latency correlated with client blocking.",
            cycle_id="heartbeat-021",
        )
    )
    assert decision.action is SecurityAction.ALLOW
    assert decision.agent_effect is AgentEffect.USED_IN_RESTRICTED_MODE
    assert decision.security_incident_created is False
    assert decision.safe_content is not None
    assert "restricted mode" in (decision.rationale or "").lower()


def test_fail_open_disabled_config_still_restricted_for_summary():
    # A summary stage never fails closed regardless of the flag.
    from app.security.config import SecurityConfig
    from app.security.gateway import HiddenLayerGateway
    from tests.security.conftest import UnavailableAdapter

    gw = HiddenLayerGateway(UnavailableAdapter(), SecurityConfig(mode="live", fail_closed=True))
    decision = gw.inspect(
        InspectionRequest(
            stage=InspectionStage.SPECIALIST_RESPONSE,
            producer="observability_specialist",
            consumer="athena",
            content="Non-actionable interpretation of evidence.",
        )
    )
    assert decision.action is SecurityAction.ALLOW
    assert decision.agent_effect is AgentEffect.USED_IN_RESTRICTED_MODE
