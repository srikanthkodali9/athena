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
    """Report storage, security, and inference readiness plus mock/live mode."""
    settings = ctx.settings

    storage_ready = _storage_ready(ctx)
    security_ready = settings.security_ready()
    inference_ready = settings.inference_ready()
    overall = storage_ready and security_ready and inference_ready

    return {
        "request_id": request.state.request_id,
        "status": "ready" if overall else "not_ready",
        "checks": {
            "storage": {"ready": storage_ready},
            "security": {
                "ready": security_ready,
                "mode": settings.security_mode.value,
                "mock": settings.security_is_mock,
                "fail_closed": settings.security_fail_closed,
            },
            "inference": {
                "ready": inference_ready,
                "mode": settings.inference_mode.value,
                "mock": settings.inference_is_mock,
            },
        },
        "degraded_mode": settings.inference_is_mock or settings.security_is_mock,
    }


@router.get("/config/status")
async def config_status(request: Request, ctx: AppContext = Depends(get_context)) -> dict:
    """Sanitized provider, model-tier, policy, and degraded-mode status."""
    body = {"request_id": request.state.request_id}
    body.update(ctx.settings.sanitized_status())
    return body


def _storage_ready(ctx: AppContext) -> bool:
    try:
        ctx.store.list_cycles(limit=1)
        return True
    except Exception:
        return False
