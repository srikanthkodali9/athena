"""Central HiddenLayer security gateway (FR-10).

Dispatches every :class:`InspectionRequest` across all stages, maps a
HiddenLayer trust verdict to an Athena behaviour, records a sanitized security
event, and updates source-trust scores.

Fail-closed rules (PRD §14.4):

* ``tool_call`` with inspection unavailable  -> ``block``.
* ``memory_write`` / ``skill_write`` / ``runbook_write`` unavailable
  -> ``reject_persistence``.
* Any non-actionable summary (explicitly flagged) -> restricted-mode ``allow``
  with a warning.

This gateway makes *trust* decisions only. Capability enforcement (can the
resulting action cross the boundary?) is OpenShell's job and is kept in a
separate module so the two never conflate.
"""

from __future__ import annotations

import threading

from .adapters import (
    HiddenLayerAdapter,
    HiddenLayerVerdict,
    InspectionUnavailable,
    build_hiddenlayer_adapter,
)
from .config import SecurityConfig
from .contracts import (
    FAIL_CLOSED_STAGES,
    PERSISTENT_WRITE_STAGES,
    AgentEffect,
    FindingType,
    InspectionRequest,
    InspectionStage,
    SecurityAction,
    SecurityDecision,
    Severity,
)
from .events import (
    SanitizedSecurityEvent,
    SecurityLedger,
    content_fingerprint,
)
from .redaction import redact_secrets

# Default trust for a not-yet-seen source. Untrusted-by-default posture still
# starts productive sources near-trusted; findings drive scores down fast.
_DEFAULT_TRUST = 0.8


class SourceTrustStore:
    """Per-producer trust scores in ``[0.0, 1.0]``."""

    def __init__(self, default: float = _DEFAULT_TRUST) -> None:
        self._default = default
        self._scores: dict[str, float] = {}
        self._lock = threading.Lock()

    def get(self, producer: str) -> float:
        with self._lock:
            return self._scores.get(producer, self._default)

    def set(self, producer: str, value: float) -> float:
        value = max(0.0, min(1.0, value))
        with self._lock:
            self._scores[producer] = value
            return value

    def reduce_to(self, producer: str, ceiling: float) -> tuple[float, float]:
        """Clamp a producer's trust down to at most ``ceiling``."""

        with self._lock:
            before = self._scores.get(producer, self._default)
            after = min(before, max(0.0, min(1.0, ceiling)))
            self._scores[producer] = after
            return before, after


class HiddenLayerGateway:
    """The single :class:`~app.security.contracts.SecurityGateway` port."""

    def __init__(
        self,
        adapter: HiddenLayerAdapter,
        config: SecurityConfig,
        trust_store: SourceTrustStore | None = None,
        ledger: SecurityLedger | None = None,
    ) -> None:
        self._adapter = adapter
        self._config = config
        self._trust = trust_store or SourceTrustStore()
        self._ledger = ledger or SecurityLedger()

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config(cls, config: SecurityConfig | None = None) -> "HiddenLayerGateway":
        config = config or SecurityConfig.from_env()
        return cls(build_hiddenlayer_adapter(config), config)

    @property
    def ledger(self) -> SecurityLedger:
        return self._ledger

    @property
    def trust(self) -> SourceTrustStore:
        return self._trust

    # -- readiness ---------------------------------------------------------

    def readiness(self) -> dict:
        """Sanitized readiness for ``/api/v1/ready`` and ``/config/status``.

        Clearly marks mock vs live so operators are never misled about whether
        real HiddenLayer is in the path.
        """

        return {
            "component": "security_gateway",
            "mode": "live" if self._config.is_live else "mock",
            "adapter": self._adapter.name,
            "is_mock": bool(self._adapter.is_mock),
            "fail_closed": self._config.fail_closed,
            "healthy": self._adapter.health(),
        }

    # -- main port ---------------------------------------------------------

    def inspect(self, request: InspectionRequest) -> SecurityDecision:
        trust_before = self._trust.get(request.producer)
        try:
            verdict = self._adapter.inspect_content(
                request.stage, request.content, request.metadata
            )
        except InspectionUnavailable:
            return self._handle_unavailable(request, trust_before)
        return self._decide(request, verdict, trust_before)

    # -- unavailable-path (fail closed / restricted mode) ------------------

    def _handle_unavailable(
        self, request: InspectionRequest, trust_before: float
    ) -> SecurityDecision:
        must_fail_closed = (
            request.stage in FAIL_CLOSED_STAGES
            or bool(request.metadata.get("actionable"))
        )
        if must_fail_closed and self._config.fail_closed:
            if request.stage in PERSISTENT_WRITE_STAGES:
                action = SecurityAction.REJECT_PERSISTENCE
                effect = AgentEffect.PERSISTENCE_REJECTED
            else:
                action = SecurityAction.BLOCK
                effect = AgentEffect.BLOCKED
            event_id = self._record(
                request, FindingType.INSPECTION_UNAVAILABLE, Severity.HIGH, 1.0,
                action, effect, trust_before, trust_before, incident=True,
            )
            return SecurityDecision(
                action=action, severity=Severity.HIGH, confidence=1.0,
                finding=FindingType.INSPECTION_UNAVAILABLE,
                trust_score_before=trust_before, trust_score_after=trust_before,
                agent_effect=effect, security_incident_created=True,
                safe_content=None, security_event_id=event_id,
                rationale="Inspection unavailable; failed closed for actionable/persistent stage.",
            )

        # Explicitly non-actionable summary: continue in restricted mode.
        safe, _ = redact_secrets(request.content)
        return SecurityDecision(
            action=SecurityAction.ALLOW, severity=Severity.LOW, confidence=0.5,
            finding=FindingType.INSPECTION_UNAVAILABLE,
            trust_score_before=trust_before, trust_score_after=trust_before,
            agent_effect=AgentEffect.USED_IN_RESTRICTED_MODE,
            security_incident_created=False,
            safe_content=safe,
            rationale="Inspection unavailable; continuing in restricted mode for non-actionable summary.",
        )

    # -- verdict -> behaviour ---------------------------------------------

    def _decide(
        self, request: InspectionRequest, verdict: HiddenLayerVerdict, trust_before: float
    ) -> SecurityDecision:
        stage = request.stage

        # Clean content: allow, redacting any incidental secrets defensively.
        if verdict.is_clean:
            safe, secret_labels = redact_secrets(request.content)
            if secret_labels:
                after = self._trust.get(request.producer)  # no trust change for redaction
                event_id = self._record(
                    request, FindingType.SECRET_EXPOSURE, Severity.MEDIUM, 0.9,
                    SecurityAction.REDACT, AgentEffect.REDACTED, trust_before, after,
                    incident=False,
                )
                return SecurityDecision(
                    action=SecurityAction.REDACT, severity=Severity.MEDIUM, confidence=0.9,
                    finding=FindingType.SECRET_EXPOSURE, trust_score_before=trust_before,
                    trust_score_after=after, agent_effect=AgentEffect.REDACTED,
                    safe_content=safe, security_event_id=event_id,
                    rationale="Secret-like material redacted before use.",
                )
            return SecurityDecision(
                action=SecurityAction.ALLOW, severity=Severity.INFO, confidence=verdict.confidence,
                finding=FindingType.NONE, trust_score_before=trust_before,
                trust_score_after=trust_before, agent_effect=AgentEffect.USED_IN_CONTEXT,
                safe_content=request.content,
            )

        action, effect, trust_ceiling, incident = _map_action(stage, verdict.finding)
        before, after = self._trust.reduce_to(request.producer, trust_ceiling)

        if action is SecurityAction.REDACT:
            safe_content, _ = redact_secrets(request.content)
        elif action is SecurityAction.ALLOW:
            safe_content = request.content
        else:
            # exclude / quarantine / block / reject_persistence -> nothing usable
            safe_content = None

        event_id = self._record(
            request, verdict.finding, verdict.severity, verdict.confidence,
            action, effect, before, after, incident=incident,
        )
        return SecurityDecision(
            action=action, severity=verdict.severity, confidence=verdict.confidence,
            finding=verdict.finding, trust_score_before=before, trust_score_after=after,
            agent_effect=effect, security_incident_created=incident,
            safe_content=safe_content, security_event_id=event_id,
            rationale=_rationale(verdict.finding, action),
        )

    # -- sanitized event recording ----------------------------------------

    def _record(
        self,
        request: InspectionRequest,
        finding: FindingType,
        severity: Severity,
        confidence: float,
        action: SecurityAction,
        effect: AgentEffect,
        trust_before: float,
        trust_after: float,
        incident: bool,
    ) -> str:
        event_id = self._ledger.next_event_id()
        self._ledger.record_event(
            SanitizedSecurityEvent(
                security_event_id=event_id,
                cycle_id=request.cycle_id,
                stage=request.stage.value,
                producer=request.producer,
                consumer=request.consumer,
                finding=finding,
                severity=severity,
                confidence=confidence,
                action=action,
                agent_effect=effect,
                trust_score_before=trust_before,
                trust_score_after=trust_after,
                security_incident_created=incident,
                content_fingerprint=content_fingerprint(request.content),
            )
        )
        return event_id


# ---------------------------------------------------------------------------
# Response policy (PRD §14.4) — pure function, stage + finding -> behaviour.
# Returns (action, agent_effect, trust_ceiling, incident_created).
# ---------------------------------------------------------------------------

_QUARANTINE_FINDINGS = {
    FindingType.PROMPT_INJECTION,
    FindingType.POISONED_ARTIFACT,
}
_BLOCK_FINDINGS = {
    FindingType.EXFILTRATION,
    FindingType.CREDENTIAL_ACCESS,
}


def _map_action(
    stage: InspectionStage, finding: FindingType
) -> tuple[SecurityAction, AgentEffect, float, bool]:
    # Persistent writes of anything non-trivial are rejected outright — poisoned
    # skills/runbooks/memory must never persist (PRD §14.4, §21.3 step 5).
    if stage in PERSISTENT_WRITE_STAGES and finding not in {
        FindingType.NONE,
        FindingType.SUSPICIOUS_INSTRUCTION,
    }:
        return SecurityAction.REJECT_PERSISTENCE, AgentEffect.PERSISTENCE_REJECTED, 0.0, True

    if finding in _QUARANTINE_FINDINGS:
        # Injection / poisoned artifact -> quarantine, drop source trust to zero.
        return SecurityAction.QUARANTINE, AgentEffect.QUARANTINED, 0.0, True

    if finding in _BLOCK_FINDINGS:
        if stage is InspectionStage.TOOL_CALL:
            # Unsafe/exfil tool request -> block and require approval.
            return SecurityAction.BLOCK, AgentEffect.BLOCKED, 0.0, True
        # Same intent embedded in retrieved/response content -> quarantine it.
        return SecurityAction.QUARANTINE, AgentEffect.QUARANTINED, 0.0, True

    if finding is FindingType.SECRET_EXPOSURE:
        # Sensitive data -> redact and continue.
        return SecurityAction.REDACT, AgentEffect.REDACTED, 1.0, False

    if finding is FindingType.SUSPICIOUS_INSTRUCTION:
        # Suspicious retrieved instruction -> exclude and reduce source trust.
        return SecurityAction.EXCLUDE, AgentEffect.EXCLUDED_FROM_CONTEXT, 0.3, True

    # Low-risk anomaly -> log and continue.
    return SecurityAction.ALLOW, AgentEffect.USED_IN_CONTEXT, 0.6, False


def _rationale(finding: FindingType, action: SecurityAction) -> str:
    return f"HiddenLayer finding '{finding.value}' -> {action.value}."
