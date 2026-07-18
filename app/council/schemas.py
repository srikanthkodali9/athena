"""Council specialist output contracts (Pydantic v2).

Two P0 specialists — Observability (round 1) and Platform (round 2). Each schema
keeps **deterministic facts separate from the model's hypothesis** (AGENTS.md;
PRD §18.3): fact fields quote evidence, the hypothesis field is the single
interpretive claim, and every specialist must cite evidence IDs, state a
calibrated confidence, and list missing evidence.

These are richer than the core ``Finding`` (which is the cross-plane record). A
``.to_finding(...)`` converter maps each specialist output onto the core
``Finding`` the orchestrator already appends to a cycle, so no core edit is
needed. Owned by inference_engineer (``app/council``); the core-owned
``SpecialistFinding`` row referenced in the contract inventory can adopt these
fields later.
"""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.athena.schemas import Finding

# Shared confidence rubric injected into every specialist prompt and enforced
# structurally by the ``confidence`` bound. Kept verbatim so prompt and schema
# agree.
CONFIDENCE_RUBRIC = (
    "Confidence rubric (0.0-1.0): "
    "0.8-1.0 = multiple independent cited evidence items directly support the "
    "claim; 0.5-0.79 = supported by cited evidence but with a plausible "
    "alternative; 0.2-0.49 = weak or single-source support; "
    "0.0-0.19 = evidence insufficient. Never exceed 0.49 if required evidence is "
    "missing."
)


# extra="ignore" (NOT "forbid", NOT "allow"): a 9B specialist occasionally emits
# a stray extra key. "forbid" would raise ValidationError and zero the entire
# finding (the live Observability failure the coordinator traced); "allow" would
# persist the unknown key and could smuggle unvetted content downstream. "ignore"
# drops the unknown key silently — the finding survives, nothing extra is kept.
class _SpecialistBase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hypothesis: str = Field(
        description="The single interpretive claim (a hypothesis, NOT a fact)."
    )
    cited_evidence_ids: list[str] = Field(
        default_factory=list,
        description="Evidence IDs from the allowlist that support the claim.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    missing_evidence: list[str] = Field(
        default_factory=list,
        description="What evidence, if available, would raise confidence.",
    )

    @field_validator("cited_evidence_ids", "missing_evidence", mode="before")
    @classmethod
    def _coerce_list(cls, v: object) -> object:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return v


class ObservabilitySpecialistOutput(_SpecialistBase):
    """Round-1 Observability interpretation (PRD §9.3)."""

    observed_symptoms: list[str] = Field(
        default_factory=list, description="Deterministic symptoms quoted from evidence."
    )
    timeline: list[str] = Field(
        default_factory=list, description="Ordered timeline of observed events."
    )
    baseline_comparison: str = Field(
        default="", description="Current vs baseline window comparison (facts)."
    )

    @staticmethod
    def example() -> dict:
        return {
            "hypothesis": "Redis connection saturation preceded the checkout API 5xx spike.",
            "observed_symptoms": ["api 5xx rose from 0.1% to 4.2% at 14:02"],
            "timeline": ["14:00 baseline nominal", "14:02 5xx spike begins"],
            "baseline_comparison": "5xx 0.1% baseline vs 4.2% current",
            "cited_evidence_ids": ["ev_metric-api-19"],
            "confidence": 0.62,
            "missing_evidence": ["redis connection pool utilization metric"],
        }

    def to_finding(
        self, *, cycle_evidence_ids: list[str], model_id: Optional[str] = None
    ) -> Finding:
        interpretation = "; ".join(self.observed_symptoms) or self.baseline_comparison
        return Finding(
            id="find_" + uuid.uuid4().hex[:8],
            role="observability",
            hypothesis=self.hypothesis,
            interpretation=interpretation,
            confidence=self.confidence,
            cited_evidence_ids=self.cited_evidence_ids,
            missing_evidence=self.missing_evidence,
            model_id=model_id,
        )


class PlatformSpecialistOutput(_SpecialistBase):
    """Round-2 Platform interpretation (PRD §9.4)."""

    platform_findings: list[str] = Field(
        default_factory=list, description="Interpretation of k8s/deploy/config state."
    )
    relevant_objects: list[str] = Field(
        default_factory=list, description="Kubernetes objects referenced (facts)."
    )
    configuration_evidence: list[str] = Field(
        default_factory=list, description="Config/manifest evidence quoted (facts)."
    )
    dependency_implications: str = Field(default="")
    safe_verification_actions: list[str] = Field(
        default_factory=list,
        description="Read-only checks only — NEVER remediation.",
    )

    @staticmethod
    def example() -> dict:
        return {
            "hypothesis": "A deployment cut the Redis connection pool ~10x, causing connection rejections.",
            "platform_findings": ["deployment changed maxConnections 100 -> 10"],
            "relevant_objects": ["deploy/checkout-api"],
            "configuration_evidence": ["ev_diff-deploy-7"],
            "dependency_implications": "checkout-api depends on redis; pool starvation cascades to 5xx",
            "safe_verification_actions": ["read current redis pool configuration"],
            "cited_evidence_ids": ["ev_diff-deploy-7"],
            "confidence": 0.88,
            "missing_evidence": [],
        }

    def to_finding(
        self, *, cycle_evidence_ids: list[str], model_id: Optional[str] = None
    ) -> Finding:
        interpretation = "; ".join(self.platform_findings) or self.dependency_implications
        return Finding(
            id="find_" + uuid.uuid4().hex[:8],
            role="platform",
            hypothesis=self.hypothesis,
            interpretation=interpretation,
            confidence=self.confidence,
            cited_evidence_ids=self.cited_evidence_ids,
            missing_evidence=self.missing_evidence,
            model_id=model_id,
        )


def response_schema(model: type[BaseModel]) -> dict:
    """JSON schema for a specialist output, used as ``ChatRequest.response_schema``."""
    return model.model_json_schema()


_PY_TYPE_HINTS = {
    "str": "string",
    "float": "number 0.0-1.0",
    "int": "integer",
    "list": "list of string",
}


def describe_fields(model: type[BaseModel]) -> str:
    """Human/LLM-readable line-per-field key list with required markers.

    A verbose JSON-schema dump confuses a 9B model; an explicit, exact key list
    with 'required' markers makes the target shape unambiguous (Issue 1 fix)."""
    lines: list[str] = []
    for name, field in model.model_fields.items():
        anno = field.annotation
        origin = getattr(anno, "__name__", str(anno))
        if origin.startswith("list") or str(anno).startswith("list"):
            kind = "list of string"
        else:
            kind = _PY_TYPE_HINTS.get(origin, "string")
        required = field.is_required()
        lines.append(
            f"- {name} ({kind}){' [REQUIRED]' if required else ''}"
        )
    return "\n".join(lines)
