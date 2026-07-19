"""HiddenLayer-enforced inference boundary.

``SecuredInferencePort`` is the single decorator placed around Athena's actual
``InferencePort``. Every complete, fully rendered message set is inspected
before it reaches a model, and every raw response is inspected before a caller
can parse, persist, or place it into a repair prompt.

The decorator owns the bounded empty/schema retry paths in production. Inner
routers receive ``security_managed_retries=True`` and therefore cannot make a
repair model call that bypasses this boundary. Specialist Pydantic repairs are
caller-owned, but they call this same port again, so both their repair prompt
and repair response cross HiddenLayer as independent attempts.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import replace
from typing import Any, Optional

from app.athena.ports import (
    ChatMessage,
    ChatRequest,
    ChatResult,
    InferencePort,
)
from app.models.json_extract import extract_json
from app.security.contracts import (
    InspectionRequest,
    InspectionStage,
    SecurityGateway,
)


class SecurityBoundaryDenied(RuntimeError):
    """HiddenLayer rejected or invalidated a required inference transition."""

    def __init__(self, message: str, *, inference_attempts: int = 0) -> None:
        super().__init__(message)
        self.inference_attempts = inference_attempts


class SecuredInferencePort:
    """Inspect and enforce every model prompt, response, and bounded retry."""

    def __init__(self, inner: InferencePort, security: SecurityGateway) -> None:
        self._inner = inner
        self._security = security
        self.inspection_count = 0

    @property
    def inner(self) -> InferencePort:
        """Wrapped inference port, exposed for sanitized type/readiness checks."""

        return self._inner

    @property
    def model_id(self) -> Optional[str]:
        """Preserve the deterministic mock's model identifier for findings."""

        return getattr(self._inner, "model_id", None)

    async def complete(self, request: ChatRequest) -> ChatResult:
        first = await self._attempt(request)

        # One bounded retry for an empty visible answer. The empty raw response
        # has already crossed ATHENA_RESPONSE/SPECIALIST_RESPONSE above.
        result = first
        if not result.content.strip():
            try:
                retry = await self._attempt(self._direct_answer_request(request))
            except SecurityBoundaryDenied as exc:
                exc.inference_attempts += first.inference_attempts
                raise
            result = _combine_attempts(first, retry)
            if not result.content.strip():
                result.content = "{}"
                result.degraded = True

        # One bounded structural repair. Only the inspected/sanitized response
        # is placed into the repair prompt; raw rejected content never recirculates.
        if request.response_schema is not None:
            obj = extract_json(result.content)
            if not _schema_ok(obj, request.response_schema):
                prior_attempts = result.inference_attempts
                try:
                    retry = await self._attempt(
                        self._repair_request(request, result.content)
                    )
                except SecurityBoundaryDenied as exc:
                    exc.inference_attempts += prior_attempts
                    raise
                result = _combine_attempts(result, retry)
                obj = extract_json(result.content)
                if not _schema_ok(obj, request.response_schema):
                    result.degraded = True
                elif obj is not None:
                    result.content = json.dumps(obj, sort_keys=True)
            elif obj is not None:
                result.content = json.dumps(obj, sort_keys=True)

        return result

    async def _attempt(self, request: ChatRequest) -> ChatResult:
        safe_request = await self._inspect_prompt(request)
        result = await self._inner.complete(safe_request)
        result.inference_attempts = max(1, result.inference_attempts)
        await self._inspect_response(safe_request, result)
        result.security_inspections += 2
        return result

    async def _inspect_prompt(self, request: ChatRequest) -> ChatRequest:
        payload = json.dumps(
            {
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in request.messages
                ]
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        decision = await self._inspect(
            InspectionRequest(
                stage=InspectionStage.MODEL_PROMPT,
                producer=request.security_producer,
                consumer=f"model:{request.model_role}",
                content=payload,
                cycle_id=request.cycle_id,
                metadata={"model_role": request.model_role, "message_count": len(request.messages)},
            )
        )
        if not decision.is_usable() or decision.safe_content is None:
            raise SecurityBoundaryDenied(
                f"model prompt for {request.model_role} rejected "
                f"({decision.action.value})"
            )
        messages = _messages_from_safe_content(decision.safe_content)
        return replace(
            request,
            messages=messages,
            security_managed_retries=True,
        )

    async def _inspect_response(
        self, request: ChatRequest, result: ChatResult
    ) -> None:
        stage = (
            InspectionStage.SPECIALIST_RESPONSE
            if request.model_role == "specialist"
            else InspectionStage.ATHENA_RESPONSE
        )
        decision = await self._inspect(
            InspectionRequest(
                stage=stage,
                producer=f"model:{request.model_role}",
                consumer=request.security_consumer,
                content=result.content,
                cycle_id=request.cycle_id,
                metadata={
                    "model_role": request.model_role,
                    "model_id": result.model_id,
                    "raw_model_response": True,
                },
            )
        )
        if not decision.is_usable() or decision.safe_content is None:
            label = (
                "specialist response"
                if request.model_role == "specialist"
                else "model response"
            )
            raise SecurityBoundaryDenied(
                f"{label} for {request.model_role} rejected "
                f"({decision.action.value})",
                inference_attempts=result.inference_attempts,
            )
        result.content = decision.safe_content

    async def _inspect(self, request: InspectionRequest):
        outcome = self._security.inspect(request)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        self.inspection_count += 1
        return outcome

    def _direct_answer_request(self, request: ChatRequest) -> ChatRequest:
        nudge = (
            "Do NOT think or deliberate. Output the final visible answer now. "
            "If JSON was requested, output only one JSON object."
        )
        return replace(
            request,
            messages=list(request.messages)
            + [ChatMessage(role="user", content=nudge)],
            max_tokens=max(request.max_tokens, 1024),
            security_producer="inference_retry",
        )

    def _repair_request(self, request: ChatRequest, safe_bad: str) -> ChatRequest:
        required = _required_keys(request.response_schema)
        instruction = (
            "The previous reply was not valid JSON for the required schema. "
            "Return one valid JSON object only, with no prose or markdown."
        )
        if required:
            instruction += " Required keys: " + ", ".join(required) + "."
        return replace(
            request,
            messages=list(request.messages)
            + [
                ChatMessage(role="assistant", content=safe_bad[:2000]),
                ChatMessage(role="user", content=instruction),
            ],
            security_producer="inference_retry",
        )


def ensure_secured_inference(
    inference: InferencePort, security: SecurityGateway
) -> SecuredInferencePort:
    """Idempotently place the mandatory security boundary around inference."""

    if isinstance(inference, SecuredInferencePort):
        return inference
    return SecuredInferencePort(inference, security)


def _messages_from_safe_content(content: str) -> list[ChatMessage]:
    try:
        payload = json.loads(content)
        raw_messages = payload["messages"]
        if not isinstance(raw_messages, list) or not raw_messages:
            raise ValueError("messages must be a non-empty list")
        messages = [
            ChatMessage(role=str(item["role"]), content=str(item["content"]))
            for item in raw_messages
            if isinstance(item, dict)
        ]
        if len(messages) != len(raw_messages):
            raise ValueError("one or more messages were invalid")
        return messages
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SecurityBoundaryDenied(
            "model prompt could not be reconstructed after security inspection"
        ) from exc


def _combine_attempts(previous: ChatResult, current: ChatResult) -> ChatResult:
    current.prompt_tokens += previous.prompt_tokens
    current.completion_tokens += previous.completion_tokens
    current.latency_ms += previous.latency_ms
    current.inference_attempts += previous.inference_attempts
    current.security_inspections += previous.security_inspections
    return current


def _required_keys(schema: Optional[dict[str, Any]]) -> list[str]:
    if not schema:
        return []
    required = schema.get("required")
    if isinstance(required, list):
        return [str(key) for key in required]
    properties = schema.get("properties")
    return list(properties) if isinstance(properties, dict) else []


def _schema_ok(
    obj: Optional[dict[str, Any]], schema: Optional[dict[str, Any]]
) -> bool:
    if obj is None or not isinstance(obj, dict):
        return False
    return all(key in obj for key in _required_keys(schema))
