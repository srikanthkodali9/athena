"""Live HiddenLayer SDK adapter — import-guard, fail-closed, defensive mapping.

These tests never import the real ``hiddenlayer`` SDK and never touch the
network. The SDK response shape is exercised through injected fakes, and the
"SDK unavailable" path is exercised without the package installed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.security.adapters import (
    InspectionUnavailable,
    LiveHiddenLayerAdapter,
    _map_analysis_to_verdict,
    build_hiddenlayer_adapter,
)
from app.security.config import SecurityConfig
from app.security.contracts import (
    FindingType,
    InspectionRequest,
    InspectionStage,
    SecurityAction,
    Severity,
)
from app.security.gateway import HiddenLayerGateway


# --- factory selection ------------------------------------------------------

def test_factory_selects_live_adapter_for_live_mode():
    adapter = build_hiddenlayer_adapter(SecurityConfig(mode="live", client_id="x", client_secret="y"))
    assert isinstance(adapter, LiveHiddenLayerAdapter)
    assert adapter.is_mock is False
    assert adapter.name == "hiddenlayer-sdk"


def test_config_hides_secret_values_in_repr():
    cfg = SecurityConfig(mode="live", client_id="id123", client_secret="TOPSECRET", bearer_token="TOKENVAL")
    text = repr(cfg)
    assert "TOPSECRET" not in text
    assert "TOKENVAL" not in text
    assert "id123" not in text


# --- (a) SDK unavailable -> InspectionUnavailable -> gateway fails closed ----

def test_missing_credentials_raise_unavailable():
    # No creds configured: even if the SDK were importable, this must fail.
    adapter = LiveHiddenLayerAdapter(config=SecurityConfig(mode="live"))
    with pytest.raises(InspectionUnavailable):
        adapter._get_client()


def _fake_analyze_raising(*args, **kwargs):
    raise RuntimeError("network down")


def test_gateway_fails_closed_when_live_inspection_raises():
    cfg = SecurityConfig(mode="live", fail_closed=True, client_id="x", client_secret="y")
    adapter = LiveHiddenLayerAdapter(config=cfg, analyze_fn=_fake_analyze_raising)
    gateway = HiddenLayerGateway(adapter, cfg)

    tool = gateway.inspect(
        InspectionRequest(
            stage=InspectionStage.TOOL_CALL, producer="router",
            consumer="tool_executor", content="run diagnostic",
        )
    )
    assert tool.action is SecurityAction.BLOCK
    assert tool.security_incident_created is True

    write = gateway.inspect(
        InspectionRequest(
            stage=InspectionStage.SKILL_WRITE, producer="athena",
            consumer="skill_store", content="candidate skill",
        )
    )
    assert write.action is SecurityAction.REJECT_PERSISTENCE


# --- (b) defensive mapper over the REAL detector-list shape ----------------

def _detector(name: str, detected: bool, **findings) -> dict:
    """Build a detector entry matching the real analyze() response shape."""

    return {"name": name, "detected": detected, "phase": "input", "findings": findings}


def test_mapper_empty_or_no_detections_is_clean():
    assert _map_analysis_to_verdict(None, "d").finding is FindingType.NONE
    assert _map_analysis_to_verdict([], "d").finding is FindingType.NONE
    assert _map_analysis_to_verdict(
        [_detector("prompt_injection", False, probabilities=[0.0])], "d"
    ).finding is FindingType.NONE


def test_mapper_lone_prompt_injection_is_downgraded_not_quarantined():
    # The core false-positive guard: benign investigation prompt.
    analysis = [_detector("prompt_injection", True, probabilities=[1.0])]
    verdict = _map_analysis_to_verdict(analysis, "d")
    assert verdict.finding is FindingType.ANOMALY
    assert verdict.severity is Severity.LOW


def test_mapper_unapproved_url_is_exfiltration():
    analysis = [
        _detector("prompt_injection", True, probabilities=[1.0]),
        _detector("url", True, urls=["https://evil.example.com/exfil"]),
    ]
    verdict = _map_analysis_to_verdict(analysis, "d")
    assert verdict.finding is FindingType.EXFILTRATION
    assert verdict.severity is Severity.HIGH


def test_mapper_approved_url_is_low_anomaly():
    allow = frozenset({"approved-prometheus.internal"})
    analysis = [_detector("url", True, urls=["https://approved-prometheus.internal/api"])]
    verdict = _map_analysis_to_verdict(analysis, "d", allow)
    assert verdict.finding is FindingType.ANOMALY
    assert verdict.severity is Severity.LOW


def test_mapper_prompt_injection_corroborated_is_poisoned():
    analysis = [
        _detector("prompt_injection", True, probabilities=[1.0]),
        _detector("denial_of_service", True),
    ]
    verdict = _map_analysis_to_verdict(analysis, "d")
    assert verdict.finding is FindingType.POISONED_ARTIFACT
    assert verdict.severity is Severity.CRITICAL


def test_mapper_pii_is_secret_exposure():
    analysis = [_detector("personally_identifiable_information", True)]
    assert _map_analysis_to_verdict(analysis, "d").finding is FindingType.SECRET_EXPOSURE


def test_mapper_unknown_detected_detector_degrades_to_anomaly():
    # A detector we don't specifically map, but which fired, is flagged — not
    # silently treated as clean.
    analysis = [_detector("some_future_detector", True)]
    assert _map_analysis_to_verdict(analysis, "d").finding is FindingType.ANOMALY


def test_live_adapter_maps_injected_clean_response():
    cfg = SecurityConfig(mode="live", client_id="x", client_secret="y")

    def fake_analyze(metadata, input):  # noqa: A002 - SDK param name
        assert input["messages"][0]["content"]  # content passed through
        return SimpleNamespace(analysis=[_detector("prompt_injection", False, probabilities=[0.0])])

    adapter = LiveHiddenLayerAdapter(config=cfg, analyze_fn=fake_analyze)
    verdict = adapter.inspect_content(InspectionStage.RETRIEVED_CONTENT, "all clear", {})
    assert verdict.finding is FindingType.NONE
    assert adapter.health() is True


def test_live_adapter_flags_injected_exfiltration_response():
    cfg = SecurityConfig(mode="live", client_id="x", client_secret="y")

    def fake_analyze(metadata, input):  # noqa: A002
        return {
            "analysis": [
                _detector("prompt_injection", True, probabilities=[1.0]),
                _detector("url", True, urls=["https://evil.example.com/exfil"]),
            ]
        }

    adapter = LiveHiddenLayerAdapter(config=cfg, analyze_fn=fake_analyze)
    gateway = HiddenLayerGateway(adapter, cfg)
    decision = gateway.inspect(
        InspectionRequest(
            stage=InspectionStage.RETRIEVED_CONTENT, producer="runbook_repo",
            consumer="observability_specialist", content="exfil attempt ...",
        )
    )
    assert decision.action is SecurityAction.QUARANTINE
    assert decision.security_incident_created is True
