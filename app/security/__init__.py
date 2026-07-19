"""Athena runtime-security package.

Two independent enforcement layers behind typed ports:

* HiddenLayer content trust  -> :class:`HiddenLayerGateway` implementing the
  :class:`SecurityGateway` port (``inspect``). Used at every trust transition.
* OpenShell capability limits -> :class:`OpenShellEngine` (``evaluate``). Used
  before any action crosses the sandbox boundary. Never sees trust scores.

Required gateway calls (see module docstrings and the handoff):

* before retrieval / prompting:  ``gateway.inspect(InspectionRequest(stage=RETRIEVED_CONTENT|MODEL_PROMPT, ...))``
* before tool execution:         ``gateway.inspect(stage=TOOL_CALL)`` AND ``engine.evaluate(PolicyRequest(...))``
* before persistence:            ``gateway.inspect(stage=MEMORY_WRITE|SKILL_WRITE|RUNBOOK_WRITE, ...)``
"""

from .config import SecurityConfig
from .contracts import (
    ACTIONABLE_STAGES,
    FAIL_CLOSED_STAGES,
    PERSISTENT_WRITE_STAGES,
    AgentEffect,
    FindingType,
    InspectionRequest,
    InspectionStage,
    SecurityAction,
    SecurityDecision,
    SecurityGateway,
    Severity,
)
from .events import (
    SanitizedDenial,
    SanitizedSecurityEvent,
    SecurityLedger,
    content_fingerprint,
)
from .gateway import HiddenLayerGateway, SourceTrustStore
from .openshell import OpenShellEngine
from .redaction import contains_secret, redact_secrets, scan_secrets

__all__ = [
    # contracts / port
    "SecurityGateway",
    "InspectionStage",
    "InspectionRequest",
    "SecurityDecision",
    "SecurityAction",
    "Severity",
    "FindingType",
    "AgentEffect",
    "ACTIONABLE_STAGES",
    "PERSISTENT_WRITE_STAGES",
    "FAIL_CLOSED_STAGES",
    # gateway
    "HiddenLayerGateway",
    "SourceTrustStore",
    "SecurityConfig",
    # openshell
    "OpenShellEngine",
    # events
    "SecurityLedger",
    "SanitizedSecurityEvent",
    "SanitizedDenial",
    "content_fingerprint",
    # redaction
    "redact_secrets",
    "scan_secrets",
    "contains_secret",
]
