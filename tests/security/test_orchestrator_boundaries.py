"""Security decisions are enforced by the real heartbeat tool path."""

from __future__ import annotations

import asyncio

import pytest

from app.athena.ports import MockInference, NoOpSecurityGateway
from app.athena.schemas import CycleState
from app.heartbeat.lifecycle import (
    CapabilityDenied,
    HeartbeatCommand,
    HeartbeatService,
    SecurityBoundaryDenied,
)
from app.memory.store import CycleStore
from app.policies.models import PolicyDecision, PolicyEffect
from app.security.contracts import (
    AgentEffect,
    FindingType,
    InspectionStage,
    SecurityAction,
    SecurityDecision,
    Severity,
)
from app.tools.registry import build_default_registry
from tests.core.conftest import make_settings


def _rejected(action: SecurityAction) -> SecurityDecision:
    effect = (
        AgentEffect.BLOCKED
        if action is SecurityAction.BLOCK
        else AgentEffect.QUARANTINED
    )
    return SecurityDecision(
        action=action,
        severity=Severity.CRITICAL,
        confidence=1.0,
        finding=FindingType.POISONED_ARTIFACT,
        agent_effect=effect,
        security_incident_created=True,
        safe_content=None,
    )


class RejectStageGateway(NoOpSecurityGateway):
    def __init__(self, stage: InspectionStage, action: SecurityAction) -> None:
        super().__init__()
        self._stage = stage
        self._action = action

    def inspect(self, request):
        decision = super().inspect(request)
        return _rejected(self._action) if request.stage is self._stage else decision


class DenyAllOpenShell:
    def evaluate(self, request):
        return PolicyDecision(
            effect=PolicyEffect.DENY,
            reason="test denial",
            category="test_policy",
            resource_category="none",
        )


def _service(tmp_path, *, security=None, openshell=None):
    settings = make_settings(tmp_path)
    store = CycleStore(settings.state_db_path)
    service = HeartbeatService(
        store=store,
        inference=MockInference(),
        security=security or NoOpSecurityGateway(),
        registry=build_default_registry(),
        settings=settings,
        openshell=openshell,
    )
    return store, service


def test_tool_calls_and_results_cross_hiddenlayer_boundaries(tmp_path):
    gateway = NoOpSecurityGateway()
    store, service = _service(tmp_path, security=gateway)
    try:
        cycle, _ = service.trigger(HeartbeatCommand(fixture="redis-incident-001"))
        result = asyncio.run(service.run_cycle(cycle.cycle_id))

        assert result.state is CycleState.COMPLETED
        assert gateway.stages.count(InspectionStage.TOOL_CALL) == 2
        assert gateway.stages.count(InspectionStage.TOOL_RESULT) == 2
        # Six actual model attempts: four strategic/midcycle/critic and two
        # specialists. Every fully rendered prompt and raw response is inspected;
        # each parsed specialist finding then crosses one additional boundary.
        assert gateway.stages.count(InspectionStage.MODEL_PROMPT) == 6
        assert gateway.stages.count(InspectionStage.ATHENA_RESPONSE) == 4
        assert gateway.stages.count(InspectionStage.SPECIALIST_RESPONSE) == 4
        assert result.metrics.model_calls == 6
    finally:
        store.close()


def test_blocked_tool_call_never_executes_or_registers_evidence(tmp_path):
    gateway = RejectStageGateway(InspectionStage.TOOL_CALL, SecurityAction.BLOCK)
    store, service = _service(tmp_path, security=gateway)
    try:
        cycle, _ = service.trigger(HeartbeatCommand(fixture="redis-incident-001"))
        with pytest.raises(SecurityBoundaryDenied, match="TOOL_CALL"):
            asyncio.run(service.run_cycle(cycle.cycle_id))

        failed = store.get_cycle(cycle.cycle_id)
        assert failed.state is CycleState.FAILED
        assert failed.metrics.tool_calls == 0
        assert failed.evidence_ids == []
    finally:
        store.close()


def test_quarantined_tool_result_never_enters_evidence_registry(tmp_path):
    gateway = RejectStageGateway(
        InspectionStage.TOOL_RESULT, SecurityAction.QUARANTINE
    )
    store, service = _service(tmp_path, security=gateway)
    try:
        cycle, _ = service.trigger(HeartbeatCommand(fixture="redis-incident-001"))
        with pytest.raises(SecurityBoundaryDenied, match="before evidence"):
            asyncio.run(service.run_cycle(cycle.cycle_id))

        failed = store.get_cycle(cycle.cycle_id)
        assert failed.state is CycleState.FAILED
        assert failed.metrics.tool_calls == 1
        assert failed.evidence_ids == []
    finally:
        store.close()


def test_openshell_denial_stops_tool_before_execution(tmp_path):
    store, service = _service(tmp_path, openshell=DenyAllOpenShell())
    try:
        cycle, _ = service.trigger(HeartbeatCommand(fixture="redis-incident-001"))
        with pytest.raises(CapabilityDenied, match="OpenShell deny"):
            asyncio.run(service.run_cycle(cycle.cycle_id))

        failed = store.get_cycle(cycle.cycle_id)
        assert failed.state is CycleState.FAILED
        assert failed.metrics.tool_calls == 0
        assert failed.evidence_ids == []
    finally:
        store.close()


def test_blocked_model_prompt_never_reaches_inference(tmp_path):
    gateway = RejectStageGateway(InspectionStage.MODEL_PROMPT, SecurityAction.BLOCK)
    store, service = _service(tmp_path, security=gateway)
    try:
        cycle, _ = service.trigger(HeartbeatCommand(fixture="redis-incident-001"))
        with pytest.raises(SecurityBoundaryDenied, match="model prompt"):
            asyncio.run(service.run_cycle(cycle.cycle_id))

        failed = store.get_cycle(cycle.cycle_id)
        assert failed.state is CycleState.FAILED
        assert failed.metrics.model_calls == 0
        assert failed.metrics.tool_calls == 0
    finally:
        store.close()


def test_quarantined_model_response_never_reaches_investigation(tmp_path):
    gateway = RejectStageGateway(
        InspectionStage.ATHENA_RESPONSE, SecurityAction.QUARANTINE
    )
    store, service = _service(tmp_path, security=gateway)
    try:
        cycle, _ = service.trigger(HeartbeatCommand(fixture="redis-incident-001"))
        with pytest.raises(SecurityBoundaryDenied, match="model response"):
            asyncio.run(service.run_cycle(cycle.cycle_id))

        failed = store.get_cycle(cycle.cycle_id)
        assert failed.state is CycleState.FAILED
        assert failed.metrics.model_calls == 1
        assert failed.metrics.tool_calls == 0
    finally:
        store.close()


def test_blocked_specialist_response_never_becomes_a_finding(tmp_path):
    gateway = RejectStageGateway(
        InspectionStage.SPECIALIST_RESPONSE, SecurityAction.BLOCK
    )
    store, service = _service(tmp_path, security=gateway)
    try:
        cycle, _ = service.trigger(HeartbeatCommand(fixture="redis-incident-001"))
        with pytest.raises(SecurityBoundaryDenied, match="specialist response"):
            asyncio.run(service.run_cycle(cycle.cycle_id))

        failed = store.get_cycle(cycle.cycle_id)
        assert failed.state is CycleState.FAILED
        assert failed.metrics.tool_calls == 2
        assert not any(finding.role == "observability" for finding in failed.findings)
    finally:
        store.close()
