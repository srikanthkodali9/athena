"""Sanitized security-event and denial representations for API / dashboard.

Nothing here ever exposes raw malicious content, secrets, protected paths, or
full prompt bodies (api-endpoints.md; AGENTS.md security rules). Content is
reduced to a non-reversible fingerprint and coarse metadata only.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from .contracts import AgentEffect, FindingType, SecurityAction, Severity


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_fingerprint(content: str) -> str:
    """Stable, non-reversible fingerprint used instead of raw content."""

    digest = hashlib.sha256((content or "").encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


class SanitizedSecurityEvent(BaseModel):
    """A HiddenLayer finding as exposed to ``/api/v1/security/events``.

    Intentionally contains NO ``content`` field.
    """

    security_event_id: str
    cycle_id: str | None = None
    stage: str
    producer: str
    consumer: str
    finding: FindingType
    severity: Severity
    confidence: float
    action: SecurityAction
    agent_effect: AgentEffect
    trust_score_before: float | None = None
    trust_score_after: float | None = None
    security_incident_created: bool = False
    content_fingerprint: str
    created_at: str = Field(default_factory=_now)

    def to_api_dict(self) -> dict:
        return self.model_dump(mode="json")


class SanitizedDenial(BaseModel):
    """An OpenShell denial / approval-gate as exposed to ``/security/denials``.

    ``resource_category`` is a coarse label — never the literal path or host.
    """

    denial_id: str
    cycle_id: str | None = None
    role: str
    action: str
    effect: str  # deny | require_approval
    category: str
    resource_category: str
    reason: str
    created_at: str = Field(default_factory=_now)

    def to_api_dict(self) -> dict:
        return self.model_dump(mode="json")


class SecurityLedger:
    """Thread-safe in-memory store of sanitized events and denials.

    Durable persistence is core/memory's responsibility; this ledger is the
    sanitized projection the gateway and policy engine append to and the API
    reads from.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[SanitizedSecurityEvent] = []
        self._denials: list[SanitizedDenial] = []
        self._event_seq = 0
        self._denial_seq = 0

    def next_event_id(self) -> str:
        with self._lock:
            self._event_seq += 1
            return f"sec_{self._event_seq:04d}"

    def next_denial_id(self) -> str:
        with self._lock:
            self._denial_seq += 1
            return f"den_{self._denial_seq:04d}"

    def record_event(self, event: SanitizedSecurityEvent) -> None:
        with self._lock:
            self._events.append(event)

    def record_denial(self, denial: SanitizedDenial) -> None:
        with self._lock:
            self._denials.append(denial)

    def events(self) -> list[SanitizedSecurityEvent]:
        with self._lock:
            return list(self._events)

    def denials(self) -> list[SanitizedDenial]:
        with self._lock:
            return list(self._denials)

    def get_event(self, security_event_id: str) -> SanitizedSecurityEvent | None:
        with self._lock:
            for event in self._events:
                if event.security_event_id == security_event_id:
                    return event
        return None
