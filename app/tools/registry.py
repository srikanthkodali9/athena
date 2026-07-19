"""Deterministic tool registry (FR-6).

Registers approved, read-only tools that return the standard ``ToolResult``
envelope. Fixture-backed ``query_metrics`` and ``diff_deployment`` are provided
for Wave 1. Every tool is deterministic: same fixture -> same evidence IDs.

The registry does NOT decide root cause; it only produces facts. Interpretation
happens in the reasoning plane via ``Finding`` objects.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

from app.athena.schemas import Evidence, ToolResult
from app.tools import fixtures

# A tool takes a params dict and returns a ToolResult.
ToolFn = Callable[[dict[str, Any]], ToolResult]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolFn] = {}

    def register(self, name: str, fn: ToolFn) -> None:
        self._tools[name] = fn

    def names(self) -> list[str]:
        return sorted(self._tools)

    def has(self, name: str) -> bool:
        return name in self._tools

    def call(self, name: str, params: dict[str, Any] | None = None) -> ToolResult:
        if name not in self._tools:
            return ToolResult(
                tool=name,
                status="error",
                error=f"unknown_tool:{name}",
            )
        started = time.perf_counter()
        result = self._tools[name](params or {})
        if result.latency_ms is None:
            result.latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return result


# ---------------------------------------------------------------------------
# Fixture-backed tools
# ---------------------------------------------------------------------------


def _evidence_from(entry: dict[str, Any]) -> Evidence:
    return Evidence(
        id=entry["id"],
        type=entry["type"],
        source=entry["source"],
        provenance=entry.get("provenance", {}),
        data=entry.get("data", {}),
        trust="unverified",
    )


def query_metrics(params: dict[str, Any]) -> ToolResult:
    """Return deterministic metric evidence for a fixture (PRD 17.3)."""
    fixture_name = params.get("fixture", fixtures.DEFAULT_FIXTURE)
    try:
        data = fixtures.get_fixture(fixture_name)
    except KeyError as exc:
        return ToolResult(tool="query_metrics", status="error", error=str(exc))
    evidence = [_evidence_from(m) for m in data.get("metrics", [])]
    return ToolResult(
        tool="query_metrics",
        status="success",
        evidence=evidence,
        summary=f"Collected {len(evidence)} metric fact(s) for {fixture_name}.",
        provenance={"fixture": fixture_name, "source": "prometheus"},
    )


def diff_deployment(params: dict[str, Any]) -> ToolResult:
    """Return a deterministic deployment diff for a fixture (PRD 17.3)."""
    fixture_name = params.get("fixture", fixtures.DEFAULT_FIXTURE)
    try:
        data = fixtures.get_fixture(fixture_name)
    except KeyError as exc:
        return ToolResult(tool="diff_deployment", status="error", error=str(exc))
    diff = data.get("deployment_diff")
    if not diff:
        return ToolResult(
            tool="diff_deployment",
            status="success",
            evidence=[],
            summary=f"No deployment diff recorded for {fixture_name}.",
            provenance={"fixture": fixture_name, "source": "kubernetes"},
        )
    evidence = [_evidence_from(diff)]
    return ToolResult(
        tool="diff_deployment",
        status="success",
        evidence=evidence,
        summary=f"Deployment diff with {len(diff['data'].get('changed', []))} change(s).",
        provenance={"fixture": fixture_name, "source": "kubernetes"},
    )


def _fixture_evidence(
    *, tool: str, fixture_name: str, field: str, source: str
) -> ToolResult:
    """Return one fixture-backed evidence collection with a common envelope."""
    try:
        data = fixtures.get_fixture(fixture_name)
    except KeyError as exc:
        return ToolResult(tool=tool, status="error", error=str(exc))
    raw = data.get(field)
    entries = raw if isinstance(raw, list) else ([raw] if raw else [])
    evidence = [_evidence_from(entry) for entry in entries]
    return ToolResult(
        tool=tool,
        status="success",
        evidence=evidence,
        summary=f"Collected {len(evidence)} {field.replace('_', ' ')} fact(s).",
        provenance={"fixture": fixture_name, "source": source},
    )


def query_logs(params: dict[str, Any]) -> ToolResult:
    """Return deterministic, sanitized log facts for one fixture."""
    fixture_name = params.get("fixture", fixtures.DEFAULT_FIXTURE)
    return _fixture_evidence(
        tool="query_logs",
        fixture_name=fixture_name,
        field="logs",
        source="loki",
    )


def read_repository(params: dict[str, Any]) -> ToolResult:
    """Return the fixture's approved repository snapshot, never arbitrary paths."""
    fixture_name = params.get("fixture", fixtures.DEFAULT_FIXTURE)
    return _fixture_evidence(
        tool="read_repository",
        fixture_name=fixture_name,
        field="repository_snapshot",
        source="repository",
    )


def inspect_dependency(params: dict[str, Any]) -> ToolResult:
    """Return a deterministic service-dependency fact."""
    fixture_name = params.get("fixture", fixtures.DEFAULT_FIXTURE)
    return _fixture_evidence(
        tool="inspect_dependency",
        fixture_name=fixture_name,
        field="dependency",
        source="service_catalog",
    )


def calculate_blast_radius(params: dict[str, Any]) -> ToolResult:
    """Return a deterministic, fixture-derived blast-radius fact."""
    fixture_name = params.get("fixture", fixtures.DEFAULT_FIXTURE)
    return _fixture_evidence(
        tool="calculate_blast_radius",
        fixture_name=fixture_name,
        field="blast_radius",
        source="service_catalog",
    )


def security_scan(params: dict[str, Any]) -> ToolResult:
    """Expose the monitored security signal as a fact, not a trust decision.

    HiddenLayer remains the authoritative enforcement boundary. This tool only
    packages the fixture's declared security-sensitive flag for the evidence
    plane so model prompts never need raw fixture access.
    """
    fixture_name = params.get("fixture", fixtures.DEFAULT_FIXTURE)
    try:
        data = fixtures.get_fixture(fixture_name)
    except KeyError as exc:
        return ToolResult(tool="security_scan", status="error", error=str(exc))
    signal = data.get("change_signal", {})
    evidence = Evidence(
        id=f"ev_security-{fixture_name}",
        type="security_signal",
        source="monitored_input",
        provenance={"fixture": fixture_name, "field": "change_signal"},
        data={"security_sensitive": bool(signal.get("security_sensitive"))},
    )
    return ToolResult(
        tool="security_scan",
        status="success",
        evidence=[evidence],
        summary="Collected the monitored security-sensitive flag; gateway inspection is still required.",
        provenance={"fixture": fixture_name, "source": "monitored_input"},
    )


def search_memory(params: dict[str, Any]) -> ToolResult:
    """Normalize already-filtered memory records into deterministic evidence.

    The learning store/retriever owns filtering and must supply only promoted,
    non-quarantined records. The tool never opens a store or filesystem itself.
    """
    records = params.get("records", [])
    if not isinstance(records, list):
        return ToolResult(
            tool="search_memory",
            status="error",
            error="records must be a list of validated memory summaries",
        )
    evidence: list[Evidence] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        canonical = json.dumps(record, sort_keys=True, default=str)
        suffix = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
        evidence.append(
            Evidence(
                id=f"ev_memory-{suffix}",
                type="memory",
                source="validated_memory",
                provenance={
                    "skill_id": record.get("skill_id"),
                    "version": record.get("version"),
                },
                data=dict(record),
                trust="validated",
            )
        )
    return ToolResult(
        tool="search_memory",
        status="success",
        evidence=evidence,
        summary=f"Collected {len(evidence)} validated memory record(s).",
        provenance={"source": "validated_memory"},
    )


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("query_metrics", query_metrics)
    registry.register("query_logs", query_logs)
    registry.register("diff_deployment", diff_deployment)
    registry.register("read_repository", read_repository)
    registry.register("inspect_dependency", inspect_dependency)
    registry.register("calculate_blast_radius", calculate_blast_radius)
    registry.register("security_scan", security_scan)
    registry.register("search_memory", search_memory)
    return registry
