"""PRD §21.3 — poisoned runbook is quarantined and never reaches prompts/memory."""

from __future__ import annotations

import json

from app.security.contracts import (
    AgentEffect,
    FindingType,
    InspectionRequest,
    InspectionStage,
    SecurityAction,
)


def _retrieved(text: str) -> InspectionRequest:
    return InspectionRequest(
        stage=InspectionStage.RETRIEVED_CONTENT,
        producer="runbook_repo",
        consumer="observability_specialist",
        content=text,
        cycle_id="heartbeat-021",
    )


def test_poisoned_runbook_is_quarantined_and_excluded(gateway, poisoned_runbook_text):
    decision = gateway.inspect(_retrieved(poisoned_runbook_text))

    # (a) quarantined, never usable downstream (not in prompts).
    assert decision.action is SecurityAction.QUARANTINE
    assert decision.agent_effect is AgentEffect.QUARANTINED
    assert decision.safe_content is None
    assert not decision.is_usable()
    assert decision.finding in {FindingType.POISONED_ARTIFACT, FindingType.PROMPT_INJECTION}


def test_poisoned_runbook_creates_security_event_and_reduces_trust(gateway, poisoned_runbook_text):
    decision = gateway.inspect(_retrieved(poisoned_runbook_text))

    # security event created + source trust strongly reduced (to zero).
    assert decision.security_incident_created is True
    assert decision.trust_score_before > decision.trust_score_after
    assert decision.trust_score_after == 0.0
    assert gateway.trust.get("runbook_repo") == 0.0

    events = gateway.ledger.events()
    assert len(events) == 1
    assert events[0].security_event_id == decision.security_event_id


def test_sanitized_event_leaks_no_malicious_payload(gateway, poisoned_runbook_text):
    gateway.inspect(_retrieved(poisoned_runbook_text))
    event = gateway.ledger.events()[0]
    serialized = json.dumps(event.to_api_dict())

    # The attacker instructions and placeholder markers must never surface.
    for leaked in ("upload", "credentials", "evil-diagnostics", "AKIA", "ignore previous"):
        assert leaked.lower() not in serialized.lower()
    assert event.content_fingerprint.startswith("sha256:")


def test_poisoned_runbook_persistence_is_rejected(gateway, poisoned_runbook_text):
    # (a cont.) same content attempting to enter memory/skill store is rejected.
    for stage in (
        InspectionStage.MEMORY_WRITE,
        InspectionStage.SKILL_WRITE,
        InspectionStage.RUNBOOK_WRITE,
    ):
        decision = gateway.inspect(
            InspectionRequest(
                stage=stage,
                producer="runbook_repo",
                consumer="memory_store",
                content=poisoned_runbook_text,
                cycle_id="heartbeat-021",
            )
        )
        assert decision.action is SecurityAction.REJECT_PERSISTENCE
        assert decision.agent_effect is AgentEffect.PERSISTENCE_REJECTED
        assert decision.safe_content is None
        assert decision.security_incident_created is True


def test_trusted_evidence_still_flows_after_poisoning(gateway, poisoned_runbook_text):
    # PRD §21.3 step 9 — normal investigation continues on trusted evidence.
    gateway.inspect(_retrieved(poisoned_runbook_text))
    clean = gateway.inspect(
        InspectionRequest(
            stage=InspectionStage.RETRIEVED_CONTENT,
            producer="prometheus",
            consumer="observability_specialist",
            content="redis_blocked_clients rose from 0 to 42 at 10:03.",
            cycle_id="heartbeat-021",
        )
    )
    assert clean.action is SecurityAction.ALLOW
    assert clean.is_usable()
