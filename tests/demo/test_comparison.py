"""Unit tests for the first-vs-learned comparison calc and the FR14-08 gate."""

from __future__ import annotations

from app.ui.comparison import build_snapshot, compare, evaluate


def _cycle_dump(**metrics) -> dict:
    """Minimal persisted-cycle shape the snapshot builder reads."""
    return {
        "cycle_id": metrics.pop("cycle_id", "cycle_x"),
        "fixture": metrics.pop("fixture", "redis-incident-001"),
        "decision": "ESCALATE",
        "mid_cycle_decision": metrics.pop("mid", "CONTINUE_PLAN"),
        "evidence_ids": metrics.pop("evidence_ids", ["a", "b", "c"]),
        "reused_skill_id": metrics.pop("reused_skill_id", None),
        "metrics": {
            "duration_ms": metrics.get("duration_ms", 1000.0),
            "tool_calls": metrics.get("tool_calls", 2),
            "specialists_invoked": metrics.get("specialists_invoked", 2),
            "model_calls": metrics.get("model_calls", 6),
            "total_tokens": metrics.get("total_tokens", 500),
            "inferences": metrics.get(
                "inferences",
                [
                    {"role": "strategic"}, {"role": "observability"},
                    {"role": "midcycle"}, {"role": "platform"},
                    {"role": "strategic"}, {"role": "critic"},
                ],
            ),
        },
        "findings": metrics.get(
            "findings",
            [{"role": "synthesis", "hypothesis": "x", "confidence": 0.86, "cited_evidence_ids": ["a"]}],
        ),
    }


def test_snapshot_derives_main_model_calls_and_confidence():
    snap = build_snapshot(_cycle_dump())
    assert snap["specialist_calls"] == 2
    # 6 inferences, 2 are specialists -> 4 main-model calls.
    assert snap["main_model_calls"] == 4
    assert snap["root_cause_confidence"] == 0.86
    assert snap["unsupported_hypotheses"] == 0
    assert snap["prior_skill_reused"] is False


def test_unsupported_hypothesis_counted_when_uncited():
    dump = _cycle_dump(
        findings=[
            {"role": "observability", "hypothesis": "guess", "confidence": 0.4, "cited_evidence_ids": []},
            {"role": "synthesis", "hypothesis": "x", "confidence": 0.86, "cited_evidence_ids": ["a"]},
        ]
    )
    assert build_snapshot(dump)["unsupported_hypotheses"] == 1


def test_gate_fails_when_incidents_identical():
    first = build_snapshot(_cycle_dump())
    learned = build_snapshot(_cycle_dump())
    cmp = compare(first, learned)
    ev = evaluate(cmp, min_improved=2)
    assert ev["passed"] is False
    assert ev["improved_count"] == 0


def test_gate_passes_with_genuine_improvement():
    """When a learned incident really is cheaper + reuses a skill, gate passes."""
    first = build_snapshot(_cycle_dump(specialists_invoked=2, model_calls=6))
    learned = build_snapshot(
        _cycle_dump(
            specialists_invoked=1,
            model_calls=4,
            inferences=[{"role": "strategic"}, {"role": "observability"}, {"role": "strategic"}],
            reused_skill_id="skill_redis_pool_saturation",
            findings=[{"role": "synthesis", "hypothesis": "x", "confidence": 0.9, "cited_evidence_ids": ["a"]}],
        )
    )
    cmp = compare(first, learned)
    improved = set(cmp["improved_metrics"])
    # specialist_calls, main_model_calls, confidence, and prior_skill_reused improve.
    assert {"specialist_calls", "main_model_calls", "prior_skill_reused"} <= improved
    assert evaluate(cmp, min_improved=2)["passed"] is True


def test_missing_value_is_not_counted_as_improvement():
    first = build_snapshot(_cycle_dump(duration_ms=None))
    learned = build_snapshot(_cycle_dump(duration_ms=None))
    cmp = compare(first, learned)
    time_row = next(r for r in cmp["rows"] if r["metric"] == "investigation_time_ms")
    assert time_row["improved"] is None
