"""Heartbeat routes (FR-1). Thin handlers; logic lives in ``HeartbeatService``."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.dependencies import AppContext, get_context
from app.api.errors import AthenaHTTPError, error_body
from app.athena.schemas import Cycle, TriggerKind
from app.heartbeat.lifecycle import (
    CycleConflict,
    FixtureNotAllowed,
    HeartbeatCommand,
    UnknownFixture,
)

router = APIRouter(prefix="/api/v1/heartbeats", tags=["heartbeats"])


class HeartbeatRequest(BaseModel):
    trigger: TriggerKind = TriggerKind.manual
    fixture: Optional[str] = None
    reason: Optional[str] = Field(default=None, max_length=500)


@router.post("")
async def create_heartbeat(
    request: Request,
    body: HeartbeatRequest,
    background: BackgroundTasks,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ctx: AppContext = Depends(get_context),
) -> JSONResponse:
    """Trigger a manual heartbeat. Returns 202 with cycle_id and links."""
    command = HeartbeatCommand(
        trigger=body.trigger,
        fixture=body.fixture,
        reason=body.reason,
        idempotency_key=idempotency_key,
    )
    try:
        cycle, created = ctx.heartbeat.trigger(command)
    except FixtureNotAllowed as exc:
        raise AthenaHTTPError(
            status_code=403,
            code="FIXTURE_NOT_ALLOWED",
            message=str(exc),
        )
    except UnknownFixture as exc:
        raise AthenaHTTPError(
            status_code=422,
            code="UNKNOWN_FIXTURE",
            message=str(exc),
        )
    except CycleConflict as exc:
        raise AthenaHTTPError(
            status_code=409,
            code="CYCLE_CONFLICT",
            message=str(exc),
            retryable=True,
        )

    if created:
        # Run the investigation after the 202 is returned.
        background.add_task(_run_cycle_safely, ctx, cycle.cycle_id)

    return JSONResponse(
        status_code=202,
        content={
            "request_id": request.state.request_id,
            "cycle_id": cycle.cycle_id,
            "status": cycle.state.value,
            "links": _links(cycle.cycle_id),
        },
    )


@router.get("/{cycle_id}")
async def get_heartbeat(
    request: Request,
    cycle_id: str,
    ctx: AppContext = Depends(get_context),
) -> dict:
    """Read cycle state, decision, plan summary, and metrics (FR-1, FR-7)."""
    cycle = ctx.store.get_cycle(cycle_id)
    if cycle is None:
        raise AthenaHTTPError(
            status_code=404,
            code="CYCLE_NOT_FOUND",
            message="Unknown cycle.",
        )
    return _cycle_view(request.state.request_id, cycle)


async def _run_cycle_safely(ctx: AppContext, cycle_id: str) -> None:
    try:
        await ctx.heartbeat.run_cycle(cycle_id)
    except Exception:
        # The service already persisted FAILED state; swallow here so the
        # background task does not crash the worker.
        pass


def _links(cycle_id: str) -> dict:
    return {
        "self": f"/api/v1/heartbeats/{cycle_id}",
        "events": f"/api/v1/heartbeats/{cycle_id}/events",
    }


def _cycle_view(request_id: str, cycle: Cycle) -> dict:
    plan_summary = None
    if cycle.plan is not None:
        plan_summary = {
            "plan_id": cycle.plan.plan_id,
            "max_specialists": cycle.plan.max_specialists,
            "max_tool_calls": cycle.plan.max_tool_calls,
            "max_tokens": cycle.plan.max_tokens,
            "deadline_seconds": cycle.plan.deadline_seconds,
            "revision_used": cycle.plan.revision_used,
            "platform_question": cycle.plan.platform_question,
        }
    return {
        "request_id": request_id,
        "cycle_id": cycle.cycle_id,
        "trigger": cycle.trigger.value,
        "state": cycle.state.value,
        "decision": cycle.decision.value if cycle.decision else None,
        "change_class": cycle.change_class.value if cycle.change_class else None,
        "skip_reason": cycle.skip_reason,
        "mid_cycle_decision": (
            cycle.mid_cycle_decision.value if cycle.mid_cycle_decision else None
        ),
        "plan": plan_summary,
        "evidence_ids": cycle.evidence_ids,
        "finding_count": len(cycle.findings),
        "metrics": cycle.metrics.model_dump(),
        "recovery_actions": cycle.recovery_actions,
        "error": cycle.error,
        "links": _links(cycle.cycle_id),
    }
