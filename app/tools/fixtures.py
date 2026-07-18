"""Local deterministic fixtures for core tools (Wave 1).

DEPENDENCY NOTE: the canonical Redis incident fixtures (FND-04) are owned by the
demo_engineer / security agents under ``fixtures/``. Those did not exist when
this core spine was built, so this module ships a MINIMAL, self-contained
fixture set so the tool registry is exercisable and tests are deterministic.
Reconcile these with the canonical ``fixtures/`` at integration.

Keyed by ``fixture`` name (e.g. ``redis-incident-001``). Each entry is pure data
— no interpretation.
"""

from __future__ import annotations

from typing import Any

DEFAULT_FIXTURE = "redis-incident-001"

_FIXTURES: dict[str, dict[str, Any]] = {
    "redis-incident-001": {
        "metrics": [
            {
                "id": "ev_metric-api-5xx-19",
                "type": "metric",
                "source": "prometheus",
                "provenance": {
                    "query": 'rate(http_requests_total{status=~"5.."}[5m])',
                    "window": "5m",
                    "service": "api",
                },
                "data": {"before": 0.01, "value": 0.18, "unit": "ratio"},
            },
            {
                "id": "ev_metric-redis-conn-20",
                "type": "metric",
                "source": "prometheus",
                "provenance": {
                    "query": "redis_connected_clients",
                    "window": "5m",
                    "service": "redis",
                },
                "data": {"before": 240, "value": 9900, "limit": 10000, "unit": "count"},
            },
        ],
        "deployment_diff": {
            "id": "ev_diff-api-deploy-7",
            "type": "diff",
            "source": "kubernetes",
            "provenance": {
                "object": "deployment/api",
                "from_revision": "api-1180",
                "to_revision": "api-1181",
                "committed_at": "2026-07-18T09:41:00Z",
            },
            "data": {
                "changed": [
                    {
                        "field": "spec.template.spec.containers[0].env.REDIS_POOL_MAX",
                        "from": "50",
                        "to": "500",
                    }
                ],
                "image_changed": False,
            },
        },
        "change_signal": {
            "source": "prometheus",
            "kind": "metric_threshold_breach",
            "summary": "API 5xx ratio rose from 0.01 to 0.18; redis clients near limit.",
            "security_sensitive": False,
        },
    },
    # A related, quieter incident used to demonstrate reuse; still meaningful.
    "redis-incident-002": {
        "metrics": [
            {
                "id": "ev_metric-api-5xx-31",
                "type": "metric",
                "source": "prometheus",
                "provenance": {
                    "query": 'rate(http_requests_total{status=~"5.."}[5m])',
                    "window": "5m",
                    "service": "api",
                },
                "data": {"before": 0.01, "value": 0.09, "unit": "ratio"},
            }
        ],
        "deployment_diff": {
            "id": "ev_diff-api-deploy-9",
            "type": "diff",
            "source": "kubernetes",
            "provenance": {
                "object": "deployment/api",
                "from_revision": "api-1204",
                "to_revision": "api-1205",
                "committed_at": "2026-07-19T14:02:00Z",
            },
            "data": {
                "changed": [
                    {
                        "field": "spec.template.spec.containers[0].env.REDIS_POOL_MAX",
                        "from": "120",
                        "to": "600",
                    }
                ],
                "image_changed": False,
            },
        },
        "change_signal": {
            "source": "prometheus",
            "kind": "metric_threshold_breach",
            "summary": "API 5xx ratio rose from 0.01 to 0.09 after a pool change.",
            "security_sensitive": False,
        },
    },
}


def known_fixture(name: str) -> bool:
    return name in _FIXTURES


def get_fixture(name: str) -> dict[str, Any]:
    if name not in _FIXTURES:
        raise KeyError(f"Unknown fixture: {name!r}")
    return _FIXTURES[name]
