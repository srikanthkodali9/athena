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


def test_phase_two_evidence_tool_surface_is_registered():
    reg = build_default_registry()
    assert set(reg.names()) == {
        "calculate_blast_radius",
        "diff_deployment",
        "inspect_dependency",
        "query_logs",
        "query_metrics",
        "read_repository",
        "search_memory",
        "security_scan",
    }


def test_fixture_backed_phase_two_tools_return_provenanced_facts():
    reg = build_default_registry()
    expected = {
        "query_logs": ("log", "loki"),
        "read_repository": ("repository", "repository"),
        "inspect_dependency": ("dependency", "service_catalog"),
        "calculate_blast_radius": ("blast_radius", "service_catalog"),
        "security_scan": ("security_signal", "monitored_input"),
    }
    for tool, (evidence_type, source) in expected.items():
        result = reg.call(tool, {"fixture": "redis-incident-001"})
        assert result.status == "success", tool
        assert len(result.evidence) == 1, tool
        assert result.evidence[0].type == evidence_type
        assert result.evidence[0].source == source
        assert result.evidence[0].provenance


def test_memory_search_only_normalizes_supplied_validated_records():
    reg = build_default_registry()
    record = {
        "skill_id": "sre.redis.connection-saturation",
        "version": "1.0.0",
        "status": "promoted",
    }
    first = reg.call("search_memory", {"records": [record]})
    second = reg.call("search_memory", {"records": [record]})
    assert first.status == "success"
    assert first.evidence_ids == second.evidence_ids
    assert first.evidence[0].trust == "validated"
