"""Heartbeat lifecycle: state transitions, WAIT path, idempotency, overlap."""

from __future__ import annotations

import pytest

from app.athena.schemas import CycleState, Decision, MidCycleDecision
from app.heartbeat.lifecycle import (
    CycleConflict,
    FixtureNotAllowed,
    HeartbeatCommand,
    UnknownFixture,
)


def test_step_timeout_allows_live_inference_and_repair_window():
    from app.heartbeat.lifecycle import HeartbeatService

    assert HeartbeatService._STEP_TIMEOUT_SECONDS == 180.0


async def test_no_change_wait_path_makes_no_model_calls(harness):
    """A heartbeat with no fixture is a no-change WAIT — zero model calls."""
    cycle, created = harness.service.trigger(HeartbeatCommand())
    assert created is True
    result = await harness.service.run_cycle(cycle.cycle_id)

    assert result.decision is Decision.WAIT
    assert result.state is CycleState.COMPLETED
    assert result.skip_reason is not None
    # FR-2: the WAIT path must not call the model.
    assert harness.inference.calls == 0
    assert result.metrics.model_calls == 0


async def test_full_investigation_transitions_and_evidence(harness):
    cycle, _ = harness.service.trigger(
        HeartbeatCommand(fixture="redis-incident-001", reason="demo")
    )
    result = await harness.service.run_cycle(cycle.cycle_id)

    assert result.state is CycleState.COMPLETED
    assert result.decision in (Decision.INVESTIGATE, Decision.ESCALATE)
    # Investigation path runs the model (plan + specialists + synth + critic).
    assert harness.inference.calls > 0
    assert result.metrics.tool_calls == 2
    assert result.plan is not None
    assert result.plan.bounds_ok()
    # Deterministic fixture evidence was registered on the cycle.
    assert "ev_metric-api-5xx-19" in result.evidence_ids
    assert "ev_diff-api-deploy-7" in result.evidence_ids
    # Findings cite registered evidence.
    synth = [f for f in result.findings if f.role == "synthesis"][0]
    assert set(synth.cited_evidence_ids).issubset(set(result.evidence_ids))


async def test_mid_cycle_decision_recorded(harness):
    cycle, _ = harness.service.trigger(HeartbeatCommand(fixture="redis-incident-001"))
    result = await harness.service.run_cycle(cycle.cycle_id)
    assert result.mid_cycle_decision in (
        MidCycleDecision.CONTINUE_PLAN,
        MidCycleDecision.REVISE_PLAN,
    )


async def test_idempotency_key_returns_same_cycle(harness):
    cmd = HeartbeatCommand(fixture="redis-incident-001", idempotency_key="idem-1")
    first, created_first = harness.service.trigger(cmd)
    second, created_second = harness.service.trigger(cmd)
    assert created_first is True
    assert created_second is False
    assert first.cycle_id == second.cycle_id


async def test_overlap_protection_rejects_second_active_cycle(harness):
    harness.service.trigger(HeartbeatCommand(fixture="redis-incident-001"))
    # A second trigger while the first is still active must conflict.
    with pytest.raises(CycleConflict):
        harness.service.trigger(HeartbeatCommand(fixture="redis-incident-002"))


async def test_overlap_clears_after_completion(harness):
    cycle, _ = harness.service.trigger(HeartbeatCommand(fixture="redis-incident-001"))
    await harness.service.run_cycle(cycle.cycle_id)
    # Now no active cycle; a new trigger succeeds.
    second, created = harness.service.trigger(HeartbeatCommand(fixture="redis-incident-002"))
    assert created is True


def test_unknown_fixture_rejected(harness):
    with pytest.raises(UnknownFixture):
        harness.service.trigger(HeartbeatCommand(fixture="does-not-exist"))


def test_fixture_rejected_in_production(tmp_path):
    from tests.core.conftest import make_settings
    from app.athena.ports import MockInference, NoOpSecurityGateway
    from app.heartbeat.lifecycle import HeartbeatService
    from app.memory.store import CycleStore
    from app.tools.registry import build_default_registry

    settings = make_settings(tmp_path, env="production")
    store = CycleStore(settings.state_db_path)
    try:
        service = HeartbeatService(
            store=store,
            inference=MockInference(),
            security=NoOpSecurityGateway(),
            registry=build_default_registry(),
            settings=settings,
        )
        with pytest.raises(FixtureNotAllowed):
            service.trigger(HeartbeatCommand(fixture="redis-incident-001"))
    finally:
        store.close()
