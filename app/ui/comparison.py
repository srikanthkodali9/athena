"""First-vs-learned comparison and the FR-14 improvement gate.

This module is a PURE calculation over PERSISTED cycle records. It does not
duplicate core logic — it reads the ``Cycle`` records the heartbeat service
already persisted (``metrics``, ``findings``, ``plan``) and derives the PRD
§20.1 comparison table plus the FR14-08 gate ("fail when fewer than two metrics
improve").

Nothing here is hard-coded from the UI: every number comes from a persisted
cycle dump passed in by the caller (the demo server reads it from the SQLite
store; tests read it the same way).
"""

from __future__ import annotations

from typing import Any

# Roles that are Council specialists (Nemotron-Nano); everything else recorded as
# an inference is a main-model (Nemotron-Super) call.
_SPECIALIST_ROLES = {"observability", "platform"}

# Per-metric direction: does a LOWER value mean improvement?
# key -> (label, lower_is_better)
_METRIC_DIRECTION: dict[str, tuple[str, bool]] = {
    "investigation_time_ms": ("Investigation time (ms)", True),
    "specialist_calls": ("Specialist calls", True),
    "tool_calls": ("Tool calls", True),
    "main_model_calls": ("Main-model calls", True),
    "root_cause_confidence": ("Root-cause confidence", False),
    "unsupported_hypotheses": ("Unsupported hypotheses", True),
    "prior_skill_reused": ("Prior skill reused", False),
}


def build_snapshot(cycle_dump: dict[str, Any]) -> dict[str, Any]:
    """Extract the PRD §20.1 measured metrics from a persisted ``Cycle`` dump.

    ``cycle_dump`` is ``Cycle.model_dump(mode="json")`` (or the equivalent read
    back from the store). All values are derived, never assumed.
    """
    metrics = cycle_dump.get("metrics") or {}
    inferences = metrics.get("inferences") or []
    findings = cycle_dump.get("findings") or []

    main_model_calls = sum(
        1 for i in inferences if (i.get("role") or "") not in _SPECIALIST_ROLES
    )

    # Root-cause confidence: prefer the synthesis finding, else the max finding.
    synth = [f for f in findings if (f.get("role") or "") == "synthesis"]
    if synth:
        confidence = max(float(f.get("confidence") or 0.0) for f in synth)
    elif findings:
        confidence = max(float(f.get("confidence") or 0.0) for f in findings)
    else:
        confidence = 0.0

    # Unsupported hypotheses: a finding that asserts a hypothesis while citing no
    # evidence is unsupported (deterministic; facts stay separate from claims).
    unsupported = sum(
        1
        for f in findings
        if str(f.get("hypothesis") or "").strip()
        and not (f.get("cited_evidence_ids") or [])
    )

    # Prior-skill reuse: read a persisted marker if present; absent => not reused.
    # (The learning integration sets this; the demo never fabricates it.)
    reused = bool(cycle_dump.get("reused_skill_id"))

    return {
        "cycle_id": cycle_dump.get("cycle_id"),
        "fixture": cycle_dump.get("fixture"),
        "decision": cycle_dump.get("decision"),
        "mid_cycle_decision": cycle_dump.get("mid_cycle_decision"),
        "investigation_time_ms": metrics.get("duration_ms"),
        "specialist_calls": int(metrics.get("specialists_invoked") or 0),
        "tool_calls": int(metrics.get("tool_calls") or 0),
        "main_model_calls": main_model_calls,
        "total_tokens": int(metrics.get("total_tokens") or 0),
        "root_cause_confidence": round(confidence, 4),
        "unsupported_hypotheses": unsupported,
        "prior_skill_reused": reused,
        "reused_skill_id": cycle_dump.get("reused_skill_id"),
        "evidence_count": len(cycle_dump.get("evidence_ids") or []),
    }


def _improved(key: str, first: Any, learned: Any) -> bool | None:
    """True/False if comparable, None if a value is missing."""
    if first is None or learned is None:
        return None
    _, lower_is_better = _METRIC_DIRECTION[key]
    if isinstance(first, bool) or isinstance(learned, bool):
        # Boolean metric (prior_skill_reused): improvement = False -> True.
        return bool(learned) and not bool(first)
    if lower_is_better:
        return learned < first
    return learned > first


def _delta(first: Any, learned: Any) -> Any:
    if first is None or learned is None:
        return None
    if isinstance(first, bool) or isinstance(learned, bool):
        return bool(learned) != bool(first)
    return round(learned - first, 4)


def compare(first_snapshot: dict[str, Any], learned_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build the first-vs-learned comparison table from two snapshots."""
    rows = []
    improved_metrics: list[str] = []
    regressed_metrics: list[str] = []
    for key, (label, lower_is_better) in _METRIC_DIRECTION.items():
        first = first_snapshot.get(key)
        learned = learned_snapshot.get(key)
        imp = _improved(key, first, learned)
        rows.append(
            {
                "metric": key,
                "label": label,
                "lower_is_better": lower_is_better,
                "first": first,
                "learned": learned,
                "delta": _delta(first, learned),
                "improved": imp,
            }
        )
        if imp is True:
            improved_metrics.append(key)
        elif imp is False and _delta(first, learned) not in (0, 0.0, False, None):
            regressed_metrics.append(key)

    return {
        "first": first_snapshot,
        "learned": learned_snapshot,
        "rows": rows,
        "improved_metrics": improved_metrics,
        "improved_count": len(improved_metrics),
        "regressed_metrics": regressed_metrics,
    }


def evaluate(comparison: dict[str, Any], min_improved: int = 2) -> dict[str, Any]:
    """FR14-08 gate: pass only when at least ``min_improved`` metrics improve."""
    improved = comparison.get("improved_count", 0)
    passed = improved >= min_improved
    return {
        "passed": passed,
        "min_improved": min_improved,
        "improved_count": improved,
        "improved_metrics": comparison.get("improved_metrics", []),
        "reason": (
            f"{improved} metric(s) improved (>= {min_improved} required)."
            if passed
            else f"only {improved} metric(s) improved; {min_improved} required. "
            "In pure mock mode both incidents run the identical fixed pipeline; "
            "genuine improvement requires the skill-reuse path (see fixtures/README.md). "
            "Improvement is never hard-coded."
        ),
    }


def compare_dumps(
    first_dump: dict[str, Any],
    learned_dump: dict[str, Any],
    min_improved: int = 2,
) -> dict[str, Any]:
    """Convenience: snapshots + comparison + gate from two persisted cycle dumps."""
    first_snap = build_snapshot(first_dump)
    learned_snap = build_snapshot(learned_dump)
    comparison = compare(first_snap, learned_snap)
    comparison["evaluation"] = evaluate(comparison, min_improved=min_improved)
    comparison["source"] = "persisted_cycle_records"
    return comparison
