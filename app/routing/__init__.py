"""Tiered model/tool routing (inference_engineer).

Encodes the PRD §10.2 routing matrix: deterministic work never reaches a model,
bounded specialist interpretation goes to the small council model, and strategic
/ mid-cycle / critic reasoning goes to the main Nemotron-Super model.
"""

from __future__ import annotations

from app.routing.router import (
    ROUTING_MATRIX,
    RoutingRule,
    TieredInferenceRouter,
)

__all__ = ["ROUTING_MATRIX", "RoutingRule", "TieredInferenceRouter"]
