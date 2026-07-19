"""Security dashboard routes (FR-10, FR-11).

Read-only, sanitized projections of the HiddenLayer ledger and OpenShell
denials. These routes NEVER return raw malicious content, secrets, protected
paths, or full prompt bodies — only the sanitized representations the security
package already produces (content reduced to a non-reversible fingerprint).

The ledger/denials are currently in-memory projections shared between the
gateway and the OpenShell engine; durable persistence can come later without
changing these contracts.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import AppContext, get_context
from app.api.errors import AthenaHTTPError

router = APIRouter(prefix="/api/v1/security", tags=["security"])


def _ledger(ctx: AppContext):
    return getattr(ctx.security, "ledger", None)


@router.get("/events")
async def security_events(
    request: Request, ctx: AppContext = Depends(get_context)
) -> dict:
    """List sanitized HiddenLayer findings and security incidents."""
    ledger = _ledger(ctx)
    events = ledger.events() if ledger is not None else []
    return {
        "request_id": request.state.request_id,
        "count": len(events),
        "events": [e.to_api_dict() for e in events],
    }


@router.get("/events/{security_event_id}")
async def security_event(
    request: Request,
    security_event_id: str,
    ctx: AppContext = Depends(get_context),
) -> dict:
    """Read one sanitized finding, trust change, and agent effect."""
    ledger = _ledger(ctx)
    event = ledger.get_event(security_event_id) if ledger is not None else None
    if event is None:
        raise AthenaHTTPError(
            status_code=404,
            code="SECURITY_EVENT_NOT_FOUND",
            message="Unknown security event.",
        )
    body = {"request_id": request.state.request_id}
    body.update(event.to_api_dict())
    return body


@router.get("/denials")
async def security_denials(
    request: Request, ctx: AppContext = Depends(get_context)
) -> dict:
    """List OpenShell denials and approval-gated actions."""
    ledger = _ledger(ctx)
    denials = ledger.denials() if ledger is not None else []
    return {
        "request_id": request.state.request_id,
        "count": len(denials),
        "denials": [d.to_api_dict() for d in denials],
    }
