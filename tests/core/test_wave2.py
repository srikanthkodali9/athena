"""Wave-2 tests: config bridge, inference seam, metrics + security routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.dependencies import _select_inference, build_context
from app.athena.ports import ChatResult, MockInference
from app.security.inference import SecuredInferencePort
from tests.core.conftest import make_settings


@pytest.fixture
def client(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Task 1: live-mode config bridge
# ---------------------------------------------------------------------------


def test_security_gateway_is_mock_when_settings_say_mock(tmp_path):
    settings = make_settings(tmp_path, security_mode="mock")
    ctx = build_context(settings)
    try:
        r = ctx.security.readiness()
        assert r["mode"] == "mock"
        assert r["is_mock"] is True
        assert r["adapter"] == "local-mock"
    finally:
        ctx.close()


def test_security_gateway_is_live_when_settings_say_live(tmp_path, monkeypatch):
    # The bug: SecurityConfig.from_env read bare os.environ and returned mock
    # even when the resolved settings said live. The bridge makes settings
    # authoritative, so live creds + live mode produce a live gateway.
    monkeypatch.setenv("ATHENA_SECURITY_CLIENT_ID", "test-client")
    monkeypatch.setenv("ATHENA_SECURITY_CLIENT_SECRET", "test-secret")
    settings = make_settings(tmp_path, security_mode="live")
    ctx = build_context(settings)
    try:
        r = ctx.security.readiness()
        assert r["mode"] == "live"
        assert r["is_mock"] is False
        assert r["adapter"] == "hiddenlayer-sdk"
    finally:
        ctx.close()


def test_stray_env_cannot_flip_mock_settings_to_live(tmp_path, monkeypatch):
    # Even with a live mode exported in the environment, an explicit mock
    # settings object stays mock (test isolation / fail-safe).
    monkeypatch.setenv("ATHENA_SECURITY_MODE", "live")
    settings = make_settings(tmp_path, security_mode="mock")
    ctx = build_context(settings)
    try:
        assert ctx.security.readiness()["is_mock"] is True
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# Task 2: inference selection seam
# ---------------------------------------------------------------------------


def test_inference_seam_mock(tmp_path):
    settings = make_settings(tmp_path, inference_mode="mock")
    assert isinstance(_select_inference(settings), MockInference)


def test_application_context_mandatorily_wraps_inference_with_security(tmp_path):
    settings = make_settings(tmp_path, inference_mode="mock", security_mode="mock")
    ctx = build_context(settings)
    try:
        assert isinstance(ctx.inference, SecuredInferencePort)
        assert isinstance(ctx.inference.inner, MockInference)
        assert ctx.heartbeat._inference is ctx.inference
    finally:
        ctx.close()


def test_inference_seam_live_is_guarded(tmp_path):
    # Live mode must not crash even if the router module is absent/failing; it
    # falls back to MockInference with a logged warning.
    settings = make_settings(tmp_path, inference_mode="live")
    inf = _select_inference(settings)
    assert hasattr(inf, "complete")
    try:
        import app.routing.router  # noqa: F401
        router_present = True
    except Exception:
        router_present = False
    if not router_present:
        assert isinstance(inf, MockInference)


# ---------------------------------------------------------------------------
# Task 3: ChatResult metrics fields
# ---------------------------------------------------------------------------


def test_chatresult_has_ttft_and_routing_reason():
    r = ChatResult(
        content="{}",
        model_id="nvidia/nemotron",
        role="strategic",
        prompt_tokens=10,
        completion_tokens=5,
        ttft_ms=42.0,
        routing_reason="strategic->super",
    )
    assert r.ttft_ms == 42.0
    assert r.routing_reason == "strategic->super"
    assert r.total_tokens == 15
    # Defaults remain optional for the mock path.
    assert ChatResult(content="", model_id="m", role="specialist").ttft_ms is None


# ---------------------------------------------------------------------------
# Task 4: metrics route
# ---------------------------------------------------------------------------


def test_metrics_route_reports_per_inference(client):
    r = client.post(
        "/api/v1/heartbeats",
        json={"trigger": "manual", "fixture": "redis-incident-001"},
    )
    cycle_id = r.json()["cycle_id"]

    m = client.get(f"/api/v1/metrics/cycles/{cycle_id}")
    assert m.status_code == 200
    body = m.json()
    assert body["metrics"]["model_calls"] > 0
    assert body["metrics"]["tool_calls"] == 2
    assert body["metrics"]["security_inspections"] > 0
    assert len(body["inferences"]) == body["metrics"]["model_calls"]
    first = body["inferences"][0]
    assert {"model_id", "role", "total_tokens", "ttft_ms", "routing_reason"} <= set(first)


def test_metrics_route_wait_cycle_has_no_inferences(client):
    r = client.post("/api/v1/heartbeats", json={"trigger": "manual"})
    cycle_id = r.json()["cycle_id"]
    body = client.get(f"/api/v1/metrics/cycles/{cycle_id}").json()
    assert body["metrics"]["model_calls"] == 0
    assert body["inferences"] == []


def test_metrics_route_unknown_cycle_404(client):
    r = client.get("/api/v1/metrics/cycles/cycle_missing")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "CYCLE_NOT_FOUND"


# ---------------------------------------------------------------------------
# Task 5: security dashboard routes
# ---------------------------------------------------------------------------


def test_security_events_route(client):
    r = client.get("/api/v1/security/events")
    assert r.status_code == 200
    body = r.json()
    assert "events" in body and isinstance(body["events"], list)
    assert body["count"] == len(body["events"])


def test_security_denials_route(client):
    r = client.get("/api/v1/security/denials")
    assert r.status_code == 200
    assert isinstance(r.json()["denials"], list)


def test_security_event_unknown_404(client):
    r = client.get("/api/v1/security/events/sec_9999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "SECURITY_EVENT_NOT_FOUND"


def test_config_status_includes_security_and_openshell(client):
    body = client.get("/api/v1/config/status").json()
    assert body["security"]["is_mock"] is True
    # OpenShell policy version surfaces when the policy loads.
    if "openshell" in body:
        assert "policy_version" in body["openshell"]
