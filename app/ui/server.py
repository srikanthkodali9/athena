"""Demo control-plane server (demo_engineer owned).

``create_demo_app`` wraps the real Athena app (``app.api.app.create_app``) so it
gets the exact same system / heartbeat / metrics / security routes, request-id
middleware, error handling, and a single lifespan-built ``AppContext``. On top
of that it adds:

* ``POST /api/v1/demo/run``      — drive the standard incident/security story
  and the isolated ``fixtures/codex`` workflow from the dashboard's one button.
* ``GET  /api/v1/demo/state``    — one aggregate read the dashboard renders,
  combining persisted standard-demo records with the last in-memory Codex result.
* ``GET  /api/v1/demo/comparison`` — the FR-14 first-vs-learned table + gate.
* ``POST /api/v1/demo/codex/run`` — isolated ``fixtures/codex`` workflow; no
  writes to the existing heartbeat demo state.
* ``/dashboard/``                — the static judge-facing dashboard.

It re-implements no core logic; it composes and reads. Learning views
(``/skills``, ``/runbook-proposals``) are not implemented server-side yet — the
dashboard probes them and shows a clean "pending integration" state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.app import create_app
from app.api.dependencies import AppContext, get_context
from app.athena.config import Settings
from app.athena.schemas import Cycle
from app.heartbeat.change_detection import MONITORED_SIGNALS
from app.ui import codex_demo_runner, demo_runner
from app.ui.comparison import build_snapshot, compare_dumps

_STATIC_DIR = Path(__file__).resolve().parent / "static"

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])

_DEMO_FIXTURES = (demo_runner.INCIDENT_ONE_FIXTURE, demo_runner.INCIDENT_TWO_FIXTURE)


# ---------------------------------------------------------------------------
# Persisted-record projections for the dashboard
# ---------------------------------------------------------------------------


def _cycle_detail(cycle: Cycle) -> dict[str, Any]:
    """Dashboard-facing detail built from a PERSISTED cycle (incl. plan revision)."""
    plan = None
    if cycle.plan is not None:
        plan = {
            "plan_id": cycle.plan.plan_id,
            "revision_used": cycle.plan.revision_used,
            "original_platform_question": cycle.plan.original_platform_question,
            "platform_question": cycle.plan.platform_question,
            "revision_reason": cycle.plan.revision_reason,
            "required_evidence": cycle.plan.required_evidence,
            "max_specialists": cycle.plan.max_specialists,
            "max_tool_calls": cycle.plan.max_tool_calls,
            "deadline_seconds": cycle.plan.deadline_seconds,
        }
    return {
        "cycle_id": cycle.cycle_id,
        "fixture": cycle.fixture,
        "trigger": cycle.trigger.value,
        "state": cycle.state.value,
        "decision": cycle.decision.value if cycle.decision else None,
        "change_class": cycle.change_class.value if cycle.change_class else None,
        "changes_detected": cycle.changes_detected,
        "mid_cycle_decision": (
            cycle.mid_cycle_decision.value if cycle.mid_cycle_decision else None
        ),
        "plan": plan,
        "evidence_ids": cycle.evidence_ids,
        "findings": [
            {
                "role": f.role,
                "hypothesis": f.hypothesis,
                "confidence": f.confidence,
                "cited_evidence_ids": f.cited_evidence_ids,
                "missing_evidence": f.missing_evidence,
            }
            for f in cycle.findings
        ],
        "metrics": cycle.metrics.model_dump(mode="json"),
        "started_at": cycle.started_at.isoformat() if cycle.started_at else None,
        "ended_at": cycle.ended_at.isoformat() if cycle.ended_at else None,
    }


def _latest_incident_cycles(ctx: AppContext) -> dict[str, Optional[Cycle]]:
    """Most-recent completed cycle for each demo incident fixture."""
    latest: dict[str, Optional[Cycle]] = {f: None for f in _DEMO_FIXTURES}
    for cycle in ctx.store.list_cycles(limit=200):  # newest first
        if cycle.fixture in latest and latest[cycle.fixture] is None:
            if cycle.state.value == "completed":
                latest[cycle.fixture] = cycle
    return latest


def _security_summary(ctx: AppContext) -> dict[str, Any]:
    ledger = getattr(ctx.security, "ledger", None)
    events = ledger.events() if ledger is not None else []
    denials = ledger.denials() if ledger is not None else []
    quarantines = [e for e in events if e.action.value in ("quarantine", "reject_persistence")]
    incidents = [e for e in events if e.security_incident_created]
    return {
        "inspection_count": len(events),
        "quarantine_count": len(quarantines),
        "incident_count": len(incidents),
        "denial_count": len(denials),
        "events": [e.to_api_dict() for e in events],
        "denials": [d.to_api_dict() for d in denials],
    }


def _incident_view(cycle: Cycle) -> dict[str, Any]:
    """One incident as the console renders it, straight from the cycle record."""
    dump = cycle.model_dump(mode="json")
    snap = build_snapshot(dump)
    plan = None
    if cycle.plan is not None:
        plan = {
            "revision_used": cycle.plan.revision_used,
            "original_platform_question": cycle.plan.original_platform_question,
            "platform_question": cycle.plan.platform_question,
            "revision_reason": cycle.plan.revision_reason,
        }
    signal = None
    if cycle.changes_detected:
        signal = cycle.changes_detected[0].get("summary")
    return {
        "cycle_id": cycle.cycle_id,
        "fixture": cycle.fixture,
        "trigger": cycle.trigger.value,
        "state": cycle.state.value,
        "decision": cycle.decision.value if cycle.decision else None,
        "change_class": cycle.change_class.value if cycle.change_class else None,
        "mid_cycle_decision": (
            cycle.mid_cycle_decision.value if cycle.mid_cycle_decision else None
        ),
        "signal": signal,
        "evidence_ids": cycle.evidence_ids,
        "evidence_count": len(cycle.evidence_ids),
        "model_calls": cycle.metrics.model_calls,
        "tool_calls": cycle.metrics.tool_calls,
        "specialists": cycle.metrics.specialists_invoked,
        "total_tokens": cycle.metrics.total_tokens,
        "duration_ms": cycle.metrics.duration_ms,
        "security_inspections": cycle.metrics.security_inspections,
        "confidence": snap.get("root_cause_confidence"),
        "plan": plan,
        "inferences": [i.model_dump(mode="json") for i in cycle.metrics.inferences],
    }


def _skills_view(ctx: AppContext) -> list[dict[str, Any]]:
    """Learned skills, deduplicated to distinct versions per skill id."""
    store = getattr(ctx, "learning_store", None)
    if store is None:
        return []
    by_id: dict[str, list[Any]] = {}
    for skill in store.list_skills():  # newest first
        by_id.setdefault(skill.skill_id, []).append(skill)

    out: list[dict[str, Any]] = []
    for skill_id, all_versions in by_id.items():
        seen: set[str] = set()
        versions = []
        for s in all_versions:  # newest first
            if s.version in seen:
                continue
            seen.add(s.version)
            versions.append(s)
        latest = versions[0]
        out.append(
            {
                "skill_id": skill_id,
                "title": latest.title,
                "domain": latest.domain,
                "risk": latest.risk.value,
                "confidence": latest.confidence,
                "status": latest.status.value,
                "latest_version": latest.version,
                "hiddenlayer_status": latest.hiddenlayer_status,
                "triggers": latest.triggers,
                "from_incidents": latest.provenance.created_from_incidents,
                "versions": [
                    {
                        "version": v.version,
                        "status": v.status.value,
                        "from_incidents": v.provenance.created_from_incidents,
                        "validation_passes": v.validation_passes,
                        "hiddenlayer_status": v.hiddenlayer_status,
                        "false_leads": len(v.known_false_leads),
                    }
                    for v in versions
                ],
                "replay": [
                    {
                        "fixture": r.fixture_name,
                        "kind": r.kind.value,
                        "verdict": r.verdict.value,
                        "passed": r.passed,
                    }
                    for r in latest.validation_history
                ],
            }
        )
    return out


def _integrations_view(ctx: AppContext) -> list[dict[str, Any]]:
    """The real outbound adapters and their configured/mock status."""
    s = ctx.settings

    def row(name, sub, endpoint, env, mode, status, tone):
        return {
            "name": name, "sub": sub, "endpoint": endpoint,
            "env": env, "mode": mode, "status": status, "tone": tone,
        }

    main_ok = bool(s.main_model_base_url)
    council_ok = bool(s.council_model_base_url)
    sec_ok = bool(s.security_base_url)
    prom_ok = bool(s.prometheus_base_url)
    return [
        row("NVIDIA NIM · Nemotron-Super", "strategic tier",
            "nvidia/llama-3.1-nemotron-super-49b", "ATHENA_MAIN_MODEL_BASE_URL",
            s.inference_mode.value, "configured" if main_ok else "not configured",
            "ok" if main_ok else "warn"),
        row("NVIDIA NIM · Nemotron-Nano", "council tier",
            "nvidia/nemotron-nano-9b", "ATHENA_COUNCIL_MODEL_BASE_URL",
            s.inference_mode.value, "configured" if council_ok else "not configured",
            "ok" if council_ok else "warn"),
        row("HiddenLayer", "runtime security",
            "interactions.analyze · OAuth2", "ATHENA_SECURITY_BASE_URL",
            s.security_mode.value, "configured" if sec_ok else "not configured",
            "ok" if sec_ok else "warn"),
        row("OpenShell", "capability policy",
            s.openshell_policy_path, "ATHENA_OPENSHELL_POLICY_PATH",
            "local", "active", "ok"),
        row("Prometheus", "telemetry",
            "/api/v1/query", "ATHENA_PROMETHEUS_BASE_URL",
            "fixture", "configured" if prom_ok else "not configured",
            "ok" if prom_ok else "warn"),
        row("Kubernetes", "cluster reads",
            "read-only events & diffs", "ATHENA_KUBERNETES_MODE",
            s.kubernetes_mode.value, "fixture", "dim"),
        row("OpenRouter", "model fallback",
            "OpenAI-compatible", "ATHENA_FALLBACK_MODEL_BASE_URL",
            "-", "enabled" if s.fallback_enabled else "disabled",
            "ok" if s.fallback_enabled else "dim"),
    ]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/run")
async def demo_run(
    request: Request,
    ctx: AppContext = Depends(get_context),
    min_improved: int = 2,
) -> dict[str, Any]:
    """Run both fixture workflows while keeping their state boundaries intact."""
    summary = await demo_runner.run_full_demo(ctx, min_improved=min_improved)
    codex_result = codex_demo_runner.run_codex_demo().model_dump(mode="json")
    request.app.state.codex_demo_result = codex_result
    return {
        "request_id": request.state.request_id,
        **summary,
        "codex": codex_result,
    }


@router.post("/codex/run")
async def codex_demo_run(request: Request) -> dict[str, Any]:
    """Run only the isolated ``fixtures/codex`` workflow.

    The runner does not use ``app.tools.fixtures`` and does not write cycles,
    skills, security events, or comparisons into the existing demo context.
    """
    result = codex_demo_runner.run_codex_demo().model_dump(mode="json")
    request.app.state.codex_demo_result = result
    return {
        "request_id": request.state.request_id,
        **result,
    }


@router.get("/state")
async def demo_state(
    request: Request, ctx: AppContext = Depends(get_context)
) -> dict[str, Any]:
    """Aggregate dashboard read for the two deliberately separate workflows."""
    latest = _latest_incident_cycles(ctx)
    first = latest.get(demo_runner.INCIDENT_ONE_FIXTURE)
    learned = latest.get(demo_runner.INCIDENT_TWO_FIXTURE)

    comparison = None
    if first is not None and learned is not None:
        comparison = compare_dumps(
            first.model_dump(mode="json"), learned.model_dump(mode="json")
        )

    return {
        "request_id": request.state.request_id,
        "mode": ctx.settings.sanitized_status(),
        "incident_one": _cycle_detail(first) if first else None,
        "incident_two": _cycle_detail(learned) if learned else None,
        "security": _security_summary(ctx),
        "comparison": comparison,
        "codex": getattr(request.app.state, "codex_demo_result", None),
        "has_run": first is not None,
    }


@router.get("/console")
async def demo_console(
    request: Request, ctx: AppContext = Depends(get_context), min_improved: int = 2
) -> dict[str, Any]:
    """One aggregate read that drives every page of the console.

    All values come from persisted cycle records, the learning store, the shared
    security ledger, and resolved settings — nothing is synthesized here.
    """
    completed = [c for c in ctx.store.list_cycles(limit=200) if c.state.value == "completed"]
    latest = _latest_incident_cycles(ctx)
    first = latest.get(demo_runner.INCIDENT_ONE_FIXTURE)
    learned = latest.get(demo_runner.INCIDENT_TWO_FIXTURE)

    incidents = [_incident_view(c) for c in completed]
    comparison = None
    if first is not None and learned is not None:
        comparison = compare_dumps(
            first.model_dump(mode="json"),
            learned.model_dump(mode="json"),
            min_improved=min_improved,
        )

    security = _security_summary(ctx)
    security["total_inspections"] = sum(c.metrics.security_inspections for c in completed)

    return {
        "request_id": request.state.request_id,
        "has_run": bool(incidents),
        "config": ctx.settings.sanitized_status(),
        "incidents": incidents,
        "incident_one": _incident_view(first) if first else None,
        "skills": _skills_view(ctx),
        "security": security,
        "comparison": comparison,
        "codex": getattr(request.app.state, "codex_demo_result", None),
        "heartbeat": {
            "interval_seconds": ctx.settings.heartbeat_interval_seconds,
            "signals": list(MONITORED_SIGNALS),
            "budgets": {
                "max_specialists": ctx.settings.max_specialists,
                "max_total_tokens": ctx.settings.max_total_tokens,
                "max_tool_calls": ctx.settings.max_tool_calls,
                "deadline_seconds": ctx.settings.investigation_deadline_seconds,
            },
        },
        "integrations": _integrations_view(ctx),
    }


@router.get("/comparison")
async def demo_comparison(
    request: Request, ctx: AppContext = Depends(get_context), min_improved: int = 2
) -> dict[str, Any]:
    """First-vs-learned comparison table + FR14-08 gate, from persisted records."""
    latest = _latest_incident_cycles(ctx)
    first = latest.get(demo_runner.INCIDENT_ONE_FIXTURE)
    learned = latest.get(demo_runner.INCIDENT_TWO_FIXTURE)
    if first is None or learned is None:
        return {
            "request_id": request.state.request_id,
            "available": False,
            "reason": "run the demo first (POST /api/v1/demo/run) so both incidents are persisted.",
        }
    comparison = compare_dumps(
        first.model_dump(mode="json"),
        learned.model_dump(mode="json"),
        min_improved=min_improved,
    )
    return {"request_id": request.state.request_id, "available": True, **comparison}


def create_demo_app(settings: Settings | None = None) -> FastAPI:
    """The real Athena app plus the demo control plane and static dashboard."""
    app = create_app(settings=settings)
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    async def _root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard/")

    if _STATIC_DIR.is_dir():
        app.mount("/dashboard", StaticFiles(directory=str(_STATIC_DIR), html=True), name="dashboard")
    return app
