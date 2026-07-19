"""Metrics routes (FR-12, FR-14). Per-cycle model/tool/latency/token metrics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import AppContext, get_context
from app.api.errors import AthenaHTTPError

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])


@router.get("/cycles/{cycle_id}")
async def cycle_metrics(
    request: Request,
    cycle_id: str,
    ctx: AppContext = Depends(get_context),
) -> dict:
    """Read model, tool, latency, token, and security metrics for a cycle."""
    cycle = ctx.store.get_cycle(cycle_id)
    if cycle is None:
        raise AthenaHTTPError(
            status_code=404,
            code="CYCLE_NOT_FOUND",
            message="Unknown cycle.",
        )

    m = cycle.metrics
    latencies = [i.latency_ms for i in m.inferences if i.latency_ms is not None]
    return {
        "request_id": request.state.request_id,
        "cycle_id": cycle.cycle_id,
        "state": cycle.state.value,
        "decision": cycle.decision.value if cycle.decision else None,
        "metrics": {
            "model_calls": m.model_calls,
            "tool_calls": m.tool_calls,
            "specialists_invoked": m.specialists_invoked,
            "total_tokens": m.total_tokens,
            "security_inspections": m.security_inspections,
            "duration_ms": m.duration_ms,
            "total_inference_latency_ms": round(sum(latencies), 3) if latencies else None,
        },
        # Per-inference telemetry (model_id, role, routing_reason, tokens, ttft).
        "inferences": [i.model_dump() for i in m.inferences],
    }
