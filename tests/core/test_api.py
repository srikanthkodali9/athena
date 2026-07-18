"""API behavior: health, ready, config/status, heartbeat lifecycle over HTTP."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from tests.core.conftest import make_settings


@pytest.fixture
def client(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings)
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["request_id"].startswith("req_")


def test_ready_reports_modes(client):
    r = client.get("/api/v1/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["inference"]["mock"] is True
    # Security readiness comes from the real gateway (mock adapter in tests).
    assert body["checks"]["security"]["is_mock"] is True
    assert body["checks"]["security"]["adapter"] == "local-mock"
    assert body["checks"]["storage"]["ready"] is True
    assert body["degraded_mode"] is True


def test_config_status_is_sanitized(client):
    r = client.get("/api/v1/config/status")
    assert r.status_code == 200
    text = r.text
    body = r.json()
    assert body["env"] == "development"
    assert body["fixtures_allowed"] is True
    # No secrets or raw URLs leak.
    assert "api_key" not in text.lower()
    assert "base_url" not in text.lower()
    assert body["providers"]["main_model_configured"] is False


def test_manual_heartbeat_accepted_and_completes(client):
    r = client.post(
        "/api/v1/heartbeats",
        json={"trigger": "manual", "fixture": "redis-incident-001", "reason": "demo"},
    )
    assert r.status_code == 202
    body = r.json()
    cycle_id = body["cycle_id"]
    assert cycle_id.startswith("cycle_")
    assert body["links"]["self"] == f"/api/v1/heartbeats/{cycle_id}"

    # Background task ran the investigation to completion.
    g = client.get(f"/api/v1/heartbeats/{cycle_id}")
    assert g.status_code == 200
    gb = g.json()
    assert gb["state"] == "completed"
    assert gb["decision"] in ("INVESTIGATE", "ESCALATE")
    assert gb["plan"] is not None
    assert len(gb["evidence_ids"]) == 3


def test_no_change_heartbeat_waits(client):
    r = client.post("/api/v1/heartbeats", json={"trigger": "manual"})
    assert r.status_code == 202
    cycle_id = r.json()["cycle_id"]
    g = client.get(f"/api/v1/heartbeats/{cycle_id}").json()
    assert g["state"] == "completed"
    assert g["decision"] == "WAIT"
    assert g["metrics"]["model_calls"] == 0


def test_unknown_cycle_returns_404(client):
    r = client.get("/api/v1/heartbeats/cycle_missing")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "CYCLE_NOT_FOUND"


def test_unknown_fixture_returns_422(client):
    r = client.post("/api/v1/heartbeats", json={"fixture": "nope"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "UNKNOWN_FIXTURE"


def test_fixture_rejected_in_production(tmp_path):
    settings = make_settings(tmp_path, env="production")
    app = create_app(settings=settings)
    with TestClient(app) as c:
        r = c.post("/api/v1/heartbeats", json={"fixture": "redis-incident-001"})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "FIXTURE_NOT_ALLOWED"
