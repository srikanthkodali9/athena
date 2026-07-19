"""Restart recovery from an incomplete cycle (FR-7)."""

from __future__ import annotations

import pytest

from app.athena.ports import MockInference, NoOpSecurityGateway
from app.athena.schemas import CycleState
from app.heartbeat.lifecycle import HeartbeatCommand, HeartbeatService
from app.memory.store import CycleStore
from app.tools.registry import build_default_registry


async def test_restart_resumes_without_repeating_costly_steps(harness, monkeypatch):
    cycle, _ = harness.service.trigger(HeartbeatCommand(fixture="redis-incident-001"))
    cid = cycle.cycle_id

    # Simulate a hard crash AFTER Round 1 tools but before the observability
    # step checkpoints. KeyboardInterrupt (BaseException) bypasses the service's
    # Exception handler, leaving the cycle non-terminal — like a killed process.
    async def _crash(self, ctx):
        raise KeyboardInterrupt("simulated crash")

    monkeypatch.setattr(HeartbeatService, "_step_round1_obs", _crash)
    with pytest.raises(KeyboardInterrupt):
        await harness.service.run_cycle(cid)
    monkeypatch.undo()

    # The crashed cycle is incomplete, with the early steps checkpointed.
    crashed = harness.store.get_cycle(cid)
    assert not crashed.is_terminal
    done = harness.store.completed_steps(cid)
    assert {"LOAD_STATE", "DETECT_CHANGES", "PLAN", "ROUND1_TOOLS"} <= set(done)
    assert "ROUND1_OBSERVABILITY" not in done
    # PLAN's model call and the two tool calls already happened once.
    assert harness.inference.calls == 1  # only PLAN's strategic call
    assert crashed.metrics.tool_calls == 2

    # --- Restart: fresh service objects, same durable DB ---
    store2 = CycleStore(harness.settings.state_db_path)
    inference2 = MockInference()
    try:
        service2 = HeartbeatService(
            store=store2,
            inference=inference2,
            security=NoOpSecurityGateway(),
            registry=build_default_registry(),
            settings=harness.settings,
        )
        resumed = await service2.recover()

        assert resumed is not None
        assert resumed.cycle_id == cid
        assert resumed.state is CycleState.COMPLETED
        # Costly steps completed before the crash are NOT re-run: the resumed
        # service only made the post-crash model calls (obs, mid-cycle,
        # platform, synthesis strategic + critic = 5), not PLAN again.
        assert inference2.calls == 5
        # Tools were not called a second time.
        assert resumed.metrics.tool_calls == 2
        # Evidence not duplicated (2 metrics + 1 diff = 3 unique facts).
        assert len(resumed.evidence_ids) == 3
        assert any("skipped completed step" in a for a in resumed.recovery_actions)
    finally:
        store2.close()
