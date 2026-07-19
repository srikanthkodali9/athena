"""Demo orchestration over a shared application context.

Composes the REAL components (heartbeat service, security gateway, OpenShell
engine, durable store) to drive the six-minute demo end to end and populate the
persisted store + the shared security ledger. It adds no investigation, security,
or scoring logic of its own — it calls core and reads what core persisted.

Everything runs in ONE process against ONE ``AppContext`` so the security
ledger the OpenShell drill writes to is the same ledger ``/security/events`` and
``/security/denials`` read from, and the cycles the dashboard reads come from the
same SQLite store.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from app.api.dependencies import AppContext
from app.heartbeat.lifecycle import HeartbeatCommand
from app.security.contracts import InspectionRequest, InspectionStage
from app.ui.comparison import compare_dumps

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = _REPO_ROOT / "fixtures"

INCIDENT_ONE_FIXTURE = "redis-incident-001"
INCIDENT_TWO_FIXTURE = "redis-incident-002"


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


async def run_incident(ctx: AppContext, fixture: str, reason: str) -> str:
    """Trigger and drive one heartbeat cycle to a terminal state. Returns id."""
    cycle, _created = ctx.heartbeat.trigger(
        HeartbeatCommand(fixture=fixture, reason=reason)
    )
    await ctx.heartbeat.run_cycle(cycle.cycle_id)
    return cycle.cycle_id


# ---------------------------------------------------------------------------
# Security drill (§21.3) — HiddenLayer quarantine + OpenShell denials
# ---------------------------------------------------------------------------


def _read_text(rel: str) -> str:
    return (_FIXTURES / rel).read_text(encoding="utf-8")


def _read_json(rel: str) -> dict[str, Any]:
    return json.loads(_read_text(rel))


def _decision_summary(kind: str, producer: str, decision) -> dict[str, Any]:
    return {
        "check": kind,
        "producer": producer,
        "finding": getattr(decision.finding, "value", str(decision.finding)),
        "action": getattr(decision.action, "value", str(decision.action)),
        "agent_effect": getattr(decision.agent_effect, "value", str(decision.agent_effect)),
        "severity": getattr(decision.severity, "value", str(decision.severity)),
        "trust_before": decision.trust_score_before,
        "trust_after": decision.trust_score_after,
        "security_incident_created": decision.security_incident_created,
        "security_event_id": decision.security_event_id,
        "usable_downstream": decision.is_usable(),
    }


def run_security_drill(ctx: AppContext, cycle_id: Optional[str] = None) -> dict[str, Any]:
    """Exercise the poisoned-runbook quarantine and the OpenShell denials.

    Writes to the shared ledger so the security dashboard routes reflect it.
    Uses the synthetic attack fixtures; no real secret or destination is used.
    """
    results: dict[str, Any] = {"hiddenlayer": [], "openshell": [], "cycle_id": cycle_id}

    # --- HiddenLayer trust decisions -------------------------------------
    clean = _read_text("runbooks/redis_latency_clean.md")
    poisoned = _read_text("runbooks/redis_latency_poisoned.md")

    # 1. Clean runbook retrieved into context -> allowed (contrast).
    d_clean = ctx.security.inspect(
        InspectionRequest(
            stage=InspectionStage.RETRIEVED_CONTENT,
            producer="runbook:redis_latency_clean",
            consumer="investigation_context",
            content=clean,
            cycle_id=cycle_id,
        )
    )
    results["hiddenlayer"].append(
        _decision_summary("clean_runbook_retrieved", "runbook:redis_latency_clean", d_clean)
    )

    # 2. Poisoned runbook retrieved into context -> quarantine, trust -> 0.
    d_poison = ctx.security.inspect(
        InspectionRequest(
            stage=InspectionStage.RETRIEVED_CONTENT,
            producer="runbook:redis_latency_poisoned",
            consumer="investigation_context",
            content=poisoned,
            cycle_id=cycle_id,
        )
    )
    results["hiddenlayer"].append(
        _decision_summary("poisoned_runbook_retrieved", "runbook:redis_latency_poisoned", d_poison)
    )

    # 3. Attempt to persist the poisoned runbook as a skill -> rejected (blocked
    #    from memory/skill creation, §21.3 step 5).
    d_write = ctx.security.inspect(
        InspectionRequest(
            stage=InspectionStage.SKILL_WRITE,
            producer="runbook:redis_latency_poisoned",
            consumer="skill_store",
            content=poisoned,
            cycle_id=cycle_id,
        )
    )
    results["hiddenlayer"].append(
        _decision_summary("poisoned_skill_write", "runbook:redis_latency_poisoned", d_write)
    )

    # --- OpenShell capability decisions (independent of HiddenLayer) ------
    engine = ctx.openshell
    if engine is None:
        results["openshell_available"] = False
        return results
    results["openshell_available"] = True

    from app.policies.models import PolicyRequest

    cred = _read_json("attacks/credential_access.json")["policy_request"]
    egress = _read_json("attacks/unapproved_egress.json")
    egress_req = egress["policy_request"]
    approved_req = egress["approved_control_request"]

    def _openshell(kind: str, req: dict[str, Any]) -> dict[str, Any]:
        decision = engine.evaluate(
            PolicyRequest(
                role=req.get("role", "athena"),
                action=req["action"],
                resource=req.get("resource"),
                destination=req.get("destination"),
                cycle_id=req.get("cycle_id", cycle_id),
            )
        )
        return {
            "check": kind,
            "action": req["action"],
            "effect": getattr(decision.effect, "value", str(decision.effect)),
            "category": decision.category,
            "resource_category": decision.resource_category,
            "reason": decision.reason,
            "matched_rule": decision.matched_rule,
        }

    # Protected credential path -> deny (§21.3 step 7).
    results["openshell"].append(_openshell("protected_credential_access", cred))
    # Unapproved external egress -> deny (§21.3 step 8).
    results["openshell"].append(_openshell("unapproved_egress", egress_req))
    # Approved control-plane egress -> allow (independence contrast).
    results["openshell"].append(_openshell("approved_control_egress", approved_req))

    return results


# ---------------------------------------------------------------------------
# Comparison from persisted records
# ---------------------------------------------------------------------------


def comparison_from_store(
    ctx: AppContext, first_cycle_id: str, learned_cycle_id: str, min_improved: int = 2
) -> dict[str, Any]:
    """Read both cycles back from the durable store and compute FR-14."""
    first = ctx.store.get_cycle(first_cycle_id)
    learned = ctx.store.get_cycle(learned_cycle_id)
    if first is None or learned is None:
        raise ValueError("both cycles must be persisted before comparison")
    return compare_dumps(
        first.model_dump(mode="json"),
        learned.model_dump(mode="json"),
        min_improved=min_improved,
    )


# ---------------------------------------------------------------------------
# Full demo
# ---------------------------------------------------------------------------


async def run_full_demo(ctx: AppContext, min_improved: int = 2) -> dict[str, Any]:
    """Run incident one, incident two, and the security drill; return a summary.

    All numbers in the returned ``comparison`` come from persisted cycle records.
    """
    first_id = await run_incident(ctx, INCIDENT_ONE_FIXTURE, "demo: incident one")
    learned_id = await run_incident(ctx, INCIDENT_TWO_FIXTURE, "demo: incident two (reuse)")
    drill = run_security_drill(ctx, cycle_id=first_id)
    comparison = comparison_from_store(ctx, first_id, learned_id, min_improved=min_improved)
    return {
        "incident_one_cycle_id": first_id,
        "incident_two_cycle_id": learned_id,
        "security_drill": drill,
        "comparison": comparison,
    }
