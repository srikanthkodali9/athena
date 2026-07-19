"""System routes: liveness, readiness, sanitized config status."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import AppContext, get_context

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health")
async def health(request: Request) -> dict:
    """Process liveness only (no dependency checks, no secrets)."""
    return {"request_id": request.state.request_id, "status": "ok"}


@router.get("/ready")
async def ready(request: Request, ctx: AppContext = Depends(get_context)) -> dict:
    """Report storage, security, and inference readiness plus mock/live mode.

    Security readiness comes from the real gateway's own ``readiness()`` (the
    live HiddenLayer gateway reports mode/is_mock/adapter/healthy), so operators
    are never misled about whether real HiddenLayer is in the path.
    """
    settings = ctx.settings

    storage_ready = _storage_ready(ctx)
    sec = _security_readiness(ctx)
    security_ready = bool(sec.get("healthy", False))
    inference_ready = settings.inference_ready()
    overall = storage_ready and security_ready and inference_ready

    return {
        "request_id": request.state.request_id,
        "status": "ready" if overall else "not_ready",
        "checks": {
            "storage": {"ready": storage_ready},
            "security": {"ready": security_ready, **sec},
            "inference": {
                "ready": inference_ready,
                "mode": settings.inference_mode.value,
                "mock": settings.inference_is_mock,
            },
        },
        "degraded_mode": bool(sec.get("is_mock", True)) or settings.inference_is_mock,
    }


@router.get("/config/status")
async def config_status(request: Request, ctx: AppContext = Depends(get_context)) -> dict:
    """Sanitized provider, model-tier, policy, and degraded-mode status."""
    body = {"request_id": request.state.request_id}
    body.update(ctx.settings.sanitized_status())
    body["security"] = _security_readiness(ctx)
    if ctx.openshell is not None:
        body["openshell"] = ctx.openshell.readiness()
    return body


def _security_readiness(ctx: AppContext) -> dict:
    """Sanitized security readiness from the gateway (adapter/mode/is_mock)."""
    readiness = getattr(ctx.security, "readiness", None)
    if callable(readiness):
        return readiness()
    return {
        "component": "security_gateway",
        "mode": ctx.settings.security_mode.value,
        "is_mock": ctx.settings.security_is_mock,
        "healthy": True,
    }


def _storage_ready(ctx: AppContext) -> bool:
    try:
        ctx.store.list_cycles(limit=1)
        return True
    except Exception:
        return False
