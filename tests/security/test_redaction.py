"""Secret redaction — REDACT action and fingerprint safety."""

from __future__ import annotations

from app.security.contracts import (
    AgentEffect,
    FindingType,
    InspectionRequest,
    InspectionStage,
    SecurityAction,
)
from app.security.redaction import redact_secrets, scan_secrets


def test_real_looking_secret_is_detected_and_redacted():
    text = "here is the key AKIAABCDEFGHIJKLMNOP for the bucket"
    redacted, labels = redact_secrets(text)
    assert "AKIAABCDEFGHIJKLMNOP" not in redacted
    assert "[REDACTED]" in redacted
    assert labels


def test_placeholder_is_not_flagged_as_secret():
    assert scan_secrets("AWS key placeholder: AKIA_PLACEHOLDER_EXAMPLE") == []


def test_gateway_redacts_incidental_secret_in_clean_content(gateway):
    decision = gateway.inspect(
        InspectionRequest(
            stage=InspectionStage.TOOL_RESULT,
            producer="log_search",
            consumer="observability_specialist",
            content="log line: password=SuperSecretValue123 emitted by service",
        )
    )
    assert decision.action is SecurityAction.REDACT
    assert decision.agent_effect is AgentEffect.REDACTED
    assert decision.finding is FindingType.SECRET_EXPOSURE
    assert "SuperSecretValue123" not in (decision.safe_content or "")
    assert decision.is_usable()
