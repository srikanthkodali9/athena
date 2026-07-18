"""Change detection (FR-2).

Deterministic, model-free classification of whether monitored inputs changed
meaningfully. For Wave 1 the signal source is the fixture's ``change_signal``;
a manual/periodic heartbeat with no fixture and no pending signals is a
no-change WAIT.

This module NEVER calls a model. It records what was checked and why an
investigation was skipped or started (FR2-05).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.athena.schemas import ChangeClass
from app.tools import fixtures


@dataclass
class ChangeDetectionResult:
    change_class: ChangeClass
    signals_checked: list[str] = field(default_factory=list)
    changes_detected: list[dict] = field(default_factory=list)
    skip_reason: Optional[str] = None
    start_reason: Optional[str] = None

    @property
    def investigation_required(self) -> bool:
        return self.change_class in (
            ChangeClass.meaningful,
            ChangeClass.security_sensitive,
            ChangeClass.requires_revalidation,
        )


# Signals a heartbeat inspects (PRD 12.1). Wave 1 evaluates the fixture-backed
# subset deterministically.
MONITORED_SIGNALS = [
    "metrics",
    "kubernetes_events",
    "repository_changes",
    "runbook_changes",
    "security_findings",
    "pending_tasks",
]


def detect_changes(fixture: Optional[str]) -> ChangeDetectionResult:
    """Classify change for this heartbeat.

    - With a known fixture: read its ``change_signal`` and classify meaningful or
      security-sensitive.
    - Without a fixture: nothing changed -> irrelevant -> WAIT.
    """
    if not fixture:
        return ChangeDetectionResult(
            change_class=ChangeClass.irrelevant,
            signals_checked=list(MONITORED_SIGNALS),
            changes_detected=[],
            skip_reason="no_fixture_and_no_pending_signals",
        )

    data = fixtures.get_fixture(fixture)
    signal = data.get("change_signal", {})
    change = {
        "source": signal.get("source", "unknown"),
        "kind": signal.get("kind", "unknown"),
        "summary": signal.get("summary", ""),
    }
    change_class = (
        ChangeClass.security_sensitive
        if signal.get("security_sensitive")
        else ChangeClass.meaningful
    )
    return ChangeDetectionResult(
        change_class=change_class,
        signals_checked=list(MONITORED_SIGNALS),
        changes_detected=[change],
        start_reason=f"{change['kind']} from {change['source']}",
    )
