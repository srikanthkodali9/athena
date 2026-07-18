"""Deterministic tool registry (FR-6).

Registers approved, read-only tools that return the standard ``ToolResult``
envelope. Fixture-backed ``query_metrics`` and ``diff_deployment`` are provided
for Wave 1. Every tool is deterministic: same fixture -> same evidence IDs.

The registry does NOT decide root cause; it only produces facts. Interpretation
happens in the reasoning plane via ``Finding`` objects.
"""

from __future__ import annotations

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


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("query_metrics", query_metrics)
    registry.register("diff_deployment", diff_deployment)
    return registry
