"""Deterministic tool registry envelopes and provenance (FR-6)."""

from __future__ import annotations

from app.tools.registry import build_default_registry


def test_query_metrics_returns_evidence_envelope():
    reg = build_default_registry()
    result = reg.call("query_metrics", {"fixture": "redis-incident-001"})
    assert result.status == "success"
    assert result.tool == "query_metrics"
    assert result.latency_ms is not None
    assert "ev_metric-api-5xx-19" in result.evidence_ids
    ev = result.evidence[0]
    # Provenance is present; interpretation is not (facts only).
    assert ev.source == "prometheus"
    assert "query" in ev.provenance


def test_diff_deployment_is_deterministic():
    reg = build_default_registry()
    a = reg.call("diff_deployment", {"fixture": "redis-incident-001"})
    b = reg.call("diff_deployment", {"fixture": "redis-incident-001"})
    assert a.evidence_ids == b.evidence_ids == ["ev_diff-api-deploy-7"]


def test_unknown_tool_returns_error_envelope():
    reg = build_default_registry()
    result = reg.call("no_such_tool")
    assert result.status == "error"
    assert "unknown_tool" in result.error


def test_unknown_fixture_returns_error():
    reg = build_default_registry()
    result = reg.call("query_metrics", {"fixture": "missing"})
    assert result.status == "error"
