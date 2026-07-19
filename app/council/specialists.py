"""Council specialist dispatch (P0: Observability + Platform).

A specialist is a **capability boundary**, not just a model: it receives one
narrow question, an allowlisted evidence bundle, explicit prohibitions, a strict
output schema, and a confidence rubric (PRD §10.4). It may not recommend final
remediation or pick arbitrary tools/endpoints.

Flow per specialist:
    1. Build the prompt from the versioned template with the narrow question and
       only the allowlisted evidence.
    2. Call the router with ``model_role="specialist"`` (-> Nemotron-Nano). The
       router already enforces the single JSON repair retry (FR5-08).
    3. Validate the JSON against the specialist's Pydantic schema.
    4. Enforce the evidence-ID allowlist: citations outside the bundle are
       rejected (dropped) and confidence is capped, because a finding may only
       cite evidence it was actually given (ADR-0003 §5).

Concurrency: ``gather_specialists`` runs *independent* specialists concurrently
(asyncio.gather) for the vLLM small-model-punch story — but Observability
(round 1) and Platform (round 2) are NOT independent and must never be gathered
together; the lifecycle enforces that ordering.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any, Optional, Sequence

from pydantic import BaseModel, ValidationError

from app.athena.ports import ChatMessage, ChatRequest, InferencePort
from app.athena.schemas import Evidence, Finding
from app.council.schemas import (
    CONFIDENCE_RUBRIC,
    ObservabilitySpecialistOutput,
    PlatformSpecialistOutput,
    describe_fields,
)
from app.models.json_extract import extract_json
from app.models.prompt_loader import load_prompt
from app.security.inference import SecurityBoundaryDenied

# Cap applied when a specialist cites nothing inside its allowlist — an
# uncited/mis-cited claim cannot carry operational weight (rubric floor).
_UNCITED_CONFIDENCE_CAP = 0.19


@dataclass
class SpecialistContract:
    """The prompt contract for one specialist call (PRD §10.4)."""

    role: str
    question: str
    allowed_evidence_ids: list[str]
    allowed_tools: list[str] = field(default_factory=list)
    prohibitions: list[str] = field(
        default_factory=lambda: [
            "Do not recommend or perform final remediation.",
            "Do not infer facts that are absent from the supplied evidence.",
            "Cite every finding by its evidence ID.",
            "Return a single valid JSON object only.",
            "State explicitly what evidence is missing.",
        ]
    )
    confidence_rubric: str = CONFIDENCE_RUBRIC


@dataclass
class SpecialistOutcome:
    """Everything produced by one specialist invocation."""

    role: str
    output: BaseModel
    finding: Finding
    prompt_version: str
    model_id: Optional[str]
    degraded: bool
    rejected_citations: list[str] = field(default_factory=list)
    metrics: Any = None
    inference_attempts: int = 1
    security_inspections: int = 0


def _render_evidence(evidence: Sequence[Evidence]) -> str:
    import json

    lines = []
    for ev in evidence:
        lines.append(
            json.dumps(
                {
                    "id": ev.id,
                    "type": ev.type,
                    "source": ev.source,
                    "data": ev.data,
                },
                default=str,
                ensure_ascii=False,
            )
        )
    return "\n".join(lines) if lines else "(no evidence supplied)"


class Specialist:
    """Base dispatcher; subclasses bind a template name and output schema."""

    prompt_name: str
    output_model: type[BaseModel]
    role: str

    def __init__(self, inference: InferencePort) -> None:
        self._inference = inference

    def build_contract(
        self, question: str, evidence: Sequence[Evidence], allowed_tools: list[str] | None = None
    ) -> SpecialistContract:
        return SpecialistContract(
            role=self.role,
            question=question,
            allowed_evidence_ids=[e.id for e in evidence],
            allowed_tools=allowed_tools or [],
        )

    def _build_messages(
        self, contract: SpecialistContract, evidence: Sequence[Evidence]
    ) -> tuple[list[ChatMessage], str]:
        template = load_prompt("council", self.prompt_name)
        import json

        body = template.render(
            role=contract.role,
            question=contract.question,
            allowed_evidence=", ".join(contract.allowed_evidence_ids) or "(none)",
            allowed_tools=", ".join(contract.allowed_tools) or "(none — interpretation only)",
            evidence_json=_render_evidence(evidence),
            prohibitions="\n".join(f"- {p}" for p in contract.prohibitions),
            confidence_rubric=contract.confidence_rubric,
            output_keys=describe_fields(self.output_model),
            output_example=json.dumps(self.output_model.example(), indent=2),  # type: ignore[attr-defined]
        )
        return [ChatMessage(role="user", content=body)], template.version

    async def run(
        self,
        question: str,
        evidence: Sequence[Evidence],
        *,
        max_tokens: int = 1600,
        allowed_tools: list[str] | None = None,
        cycle_id: str | None = None,
    ) -> SpecialistOutcome:
        contract = self.build_contract(question, evidence, allowed_tools)
        messages, version = self._build_messages(contract, evidence)
        # NOTE: we pass response_schema=None and own validation + the SINGLE
        # repair here. A 9B model's failure mode is a *Pydantic* mismatch (wrong
        # type / missing field), which the router's key-presence check cannot
        # catch — so the specialist re-prompts with the EXACT validation error,
        # keeping to one structured-output repair retry (FR5-08).
        request = ChatRequest(
            messages=list(messages),
            model_role="specialist",
            max_tokens=max_tokens,
            temperature=0.0,
            # Nemotron-Nano occasionally streams a long reasoning trace; give it
            # generous headroom so a slow-but-valid answer is not turned into a
            # spurious InferenceUnavailable at the default 30s (live flake fix).
            timeout_seconds=90.0,
            cycle_id=cycle_id,
            security_producer=f"specialist:{self.role}",
            security_consumer="specialist_dispatcher",
        )
        result = await self._inference.complete(request)
        inference_attempts = result.inference_attempts
        security_inspections = result.security_inspections
        degraded = bool(getattr(result, "degraded", False))
        metrics = getattr(result, "metrics", None)

        output, val_error = self._try_validate(result.content)
        if output is None:
            # ONE targeted Pydantic-aware repair retry (distinct from any JSON
            # parse repair): show the model exactly what was wrong.
            repair_req = self._repair_request(
                request, result.content, val_error, max_tokens
            )
            try:
                result = await self._inference.complete(repair_req)
            except SecurityBoundaryDenied as exc:
                exc.inference_attempts += inference_attempts
                raise
            inference_attempts += result.inference_attempts
            security_inspections += result.security_inspections
            metrics = getattr(result, "metrics", None) or metrics
            output, val_error = self._try_validate(result.content)
            if output is None:
                output = self._empty_output()
                degraded = True

        allowed = set(contract.allowed_evidence_ids)
        output, rejected = _enforce_allowlist(output, allowed)
        if rejected:
            degraded = True

        finding = output.to_finding(  # type: ignore[attr-defined]
            cycle_evidence_ids=list(allowed), model_id=result.model_id
        )
        return SpecialistOutcome(
            role=self.role,
            output=output,
            finding=finding,
            prompt_version=version,
            model_id=result.model_id,
            degraded=degraded,
            rejected_citations=rejected,
            metrics=metrics,
            inference_attempts=inference_attempts,
            security_inspections=security_inspections,
        )

    def _try_validate(
        self, content: str
    ) -> tuple[Optional[BaseModel], Optional[ValidationError]]:
        obj = extract_json(content)
        if obj is None:
            return None, None
        try:
            return self.output_model.model_validate(obj), None
        except ValidationError as exc:
            return None, exc

    def _repair_request(
        self,
        original: ChatRequest,
        bad_content: str,
        error: Optional[ValidationError],
        max_tokens: int,
    ) -> ChatRequest:
        if error is not None:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}"
                for e in error.errors()[:8]
            )
            detail = f"Validation errors to fix: {problems}."
        else:
            detail = "Your previous reply contained no parseable JSON object."
        import json

        instruction = (
            f"{detail}\n"
            "Reply with a SINGLE valid JSON object ONLY — no prose, no markdown "
            "fences, no <think> block — using EXACTLY these keys and no others:\n"
            f"{describe_fields(self.output_model)}\n"
            "Example shape:\n"
            f"{json.dumps(self.output_model.example(), indent=2)}"  # type: ignore[attr-defined]
        )
        messages = list(original.messages) + [
            ChatMessage(role="assistant", content=bad_content[:1500]),
            ChatMessage(role="user", content=instruction),
        ]
        return replace(
            original,
            messages=messages,
            max_tokens=max_tokens,
            security_producer="specialist_repair",
        )

    def _empty_output(self) -> BaseModel:
        return self.output_model.model_validate(
            {"hypothesis": "", "confidence": 0.0, "cited_evidence_ids": [], "missing_evidence": ["inference degraded"]}
        )


class ObservabilitySpecialist(Specialist):
    prompt_name = "observability"
    output_model = ObservabilitySpecialistOutput
    role = "observability"


class PlatformSpecialist(Specialist):
    prompt_name = "platform"
    output_model = PlatformSpecialistOutput
    role = "platform"


def _enforce_allowlist(
    output: BaseModel, allowed: set[str]
) -> tuple[BaseModel, list[str]]:
    """Drop citations outside the allowlist; cap confidence if nothing valid
    remains. Returns the possibly-updated output and the rejected IDs."""
    cited = list(getattr(output, "cited_evidence_ids", []))
    rejected = [c for c in cited if c not in allowed]
    if not rejected:
        return output, []
    kept = [c for c in cited if c in allowed]
    update: dict[str, Any] = {"cited_evidence_ids": kept}
    if not kept:
        update["confidence"] = min(
            getattr(output, "confidence", 0.0), _UNCITED_CONFIDENCE_CAP
        )
    return output.model_copy(update=update), rejected


async def gather_specialists(*coros: Any) -> list[SpecialistOutcome]:
    """Run independent specialist coroutines concurrently (asyncio.gather).

    Only pass specialists that belong to the SAME round. Observability (round 1)
    and Platform (round 2) are sequenced by the lifecycle and must not be passed
    together (ADR-0003).
    """
    return list(await asyncio.gather(*coros))
