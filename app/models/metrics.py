"""Per-call inference metrics (FR4-04, FR12-06).

``ChatResult`` in the core-owned ``app/athena/ports.py`` currently exposes
``model_id``, ``role``, ``prompt_tokens``, ``completion_tokens``, ``latency_ms``
and ``degraded`` — but not ``ttft_ms`` or ``routing_reason``. Until core adds
those fields (requested in the handoff), the router attaches this richer object
to every result and appends it to an in-memory metrics log so the dashboard and
tests can read the full record without editing core.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class InferenceMetrics:
    """Everything recorded for a single inference (one model call)."""

    model_id: str
    role: str
    routing_reason: str
    tier: str  # "main" | "council" | "fallback"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ttft_ms: Optional[float] = None
    latency_ms: float = 0.0
    degraded: bool = False
    repaired: bool = False
    attempts: int = 1
    finish_reason: Optional[str] = None
    reasoning_tokens_estimate: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def tokens_per_second(self) -> Optional[float]:
        if self.latency_ms <= 0 or self.completion_tokens <= 0:
            return None
        return round(self.completion_tokens / (self.latency_ms / 1000.0), 2)

    def as_dict(self) -> dict:
        data = asdict(self)
        data["total_tokens"] = self.total_tokens
        data["tokens_per_second"] = self.tokens_per_second
        return data
