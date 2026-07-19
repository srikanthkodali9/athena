"""Durable store: cycle round-trip, evidence registry, transactional checkpoints."""

from __future__ import annotations

from app.athena.schemas import (
    Checkpoint,
    Cycle,
    CycleState,
    Evidence,
    TriggerKind,
)
from app.memory.store import CycleStore


def _cycle(**overrides) -> Cycle:
    base = dict(cycle_id="cycle_test1", trigger=TriggerKind.manual)
    base.update(overrides)
    return Cycle(**base)


def test_cycle_round_trip(tmp_path):
    store = CycleStore(tmp_path / "s.db")
    try:
        c = _cycle()
        store.save_cycle(c)
        loaded = store.get_cycle("cycle_test1")
        assert loaded is not None
        assert loaded.cycle_id == "cycle_test1"
        assert loaded.state is CycleState.QUEUED
    finally:
        store.close()


def test_active_and_incomplete_cycle(tmp_path):
    store = CycleStore(tmp_path / "s.db")
    try:
        store.save_cycle(_cycle(cycle_id="cycle_a", state=CycleState.INVESTIGATING))
        store.save_cycle(_cycle(cycle_id="cycle_b", state=CycleState.COMPLETED))
        active = store.active_cycle()
        assert active is not None and active.cycle_id == "cycle_a"
        assert store.find_incomplete_cycle().cycle_id == "cycle_a"
    finally:
        store.close()


def test_idempotency_lookup(tmp_path):
    store = CycleStore(tmp_path / "s.db")
    try:
        store.save_cycle(_cycle(idempotency_key="k1"))
        assert store.get_by_idempotency_key("k1").cycle_id == "cycle_test1"
        assert store.get_by_idempotency_key("missing") is None
    finally:
        store.close()


def test_evidence_registry_is_immutable(tmp_path):
    store = CycleStore(tmp_path / "s.db")
    try:
        ev = Evidence(id="ev_1", type="metric", source="prometheus", data={"v": 1})
        store.register_evidence(ev, "cycle_test1", produced_by="query_metrics")
        # Re-registering with different data is ignored (immutable).
        ev2 = Evidence(id="ev_1", type="metric", source="prometheus", data={"v": 999})
        store.register_evidence(ev2, "cycle_test1", produced_by="query_metrics")
        loaded = store.get_evidence("ev_1")
        assert loaded is not None
        assert loaded.data == {"v": 1}
    finally:
        store.close()


def test_checkpoint_is_transactional_and_idempotent(tmp_path):
    store = CycleStore(tmp_path / "s.db")
    try:
        c = _cycle(state=CycleState.PLANNING)
        store.save_cycle(c)
        store.checkpoint(
            c, Checkpoint(cycle_id=c.cycle_id, step="PLAN", costly=True, result={"ok": 1})
        )
        # Cycle state persisted alongside the checkpoint in one transaction.
        assert store.has_step(c.cycle_id, "PLAN")
        steps = store.completed_steps(c.cycle_id)
        assert steps["PLAN"].costly is True
        assert steps["PLAN"].result == {"ok": 1}
        # Re-checkpointing the same step is a no-op insert (idempotent).
        store.checkpoint(
            c, Checkpoint(cycle_id=c.cycle_id, step="PLAN", costly=True, result={"ok": 2})
        )
        assert len(store.completed_steps(c.cycle_id)) == 1
    finally:
        store.close()
