"""TieredInferenceRouter — the live ``InferencePort`` implementation (FR-4).

Routes each :class:`ChatRequest` by ``model_role`` to the right model tier,
enforces structured JSON output with **exactly one** repair retry, and records a
full :class:`InferenceMetrics` row per call (model id, role, routing reason,
tokens, ttft, latency, degraded, repaired).

Routing matrix (PRD §10.2 / ADR-0003 six-call contract):

    strategic  -> main    (Nemotron-Super: planning + synthesis)
    midcycle   -> main    (Nemotron-Super: CONTINUE_PLAN / REVISE_PLAN)
    critic     -> main    (Nemotron-Super: adversarial self-critique)
    specialist -> council (Nemotron-Nano: bounded interpretation)

Deterministic work (parse alert / metric delta / k8s reads / manifest diffs)
never calls this router at all — it stays in ``app/tools`` — which is the first
row of the matrix ("use no LLM when deterministic logic can answer").

``ChatResult`` (core-owned) has no ``ttft_ms`` / ``routing_reason`` fields yet, so
we (1) set the fields it *does* have, (2) attach the rich metrics object as
``result.metrics`` (ChatResult is a plain dataclass, so this is safe), and
(3) append to ``self.metrics_log``. The handoff asks core to add the two fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.athena.ports import ChatMessage, ChatRequest, ChatResult, InferencePort
from app.models.errors import InferenceUnavailable
from app.models.json_extract import extract_json
from app.models.metrics import InferenceMetrics
from app.models.nim_client import NimChatClient, NimClientConfig, NimResult


@dataclass(frozen=True)
class RoutingRule:
    tier: str  # "main" | "council"
    reason: str


# The encoded routing matrix, keyed by ChatRequest.model_role.
ROUTING_MATRIX: dict[str, RoutingRule] = {
    "strategic": RoutingRule(
        "main", "strategic reasoning: planning/synthesis/contradiction (Nemotron-Super)"
    ),
    "midcycle": RoutingRule(
        "main", "mid-cycle plan evaluation CONTINUE/REVISE (Nemotron-Super)"
    ),
    "critic": RoutingRule(
        "main", "adversarial self-critique before recommendation (Nemotron-Super)"
    ),
    "specialist": RoutingRule(
        "council", "bounded specialist evidence interpretation (Nemotron-Nano)"
    ),
}

# Roles that carry operational/strategic weight — never silently downgrade the
# tier for these even under load; unknown roles fail safe to the main model.
_DEFAULT_RULE = RoutingRule("main", "unrecognized role -> strategic default (fail safe)")


class TieredInferenceRouter(InferencePort):
    """Async OpenAI-compatible router over a main + council NIM client."""

    def __init__(
        self,
        *,
        main: InferencePort | NimChatClient,
        council: InferencePort | NimChatClient,
        degraded_fallback: Optional[InferencePort] = None,
        reasoning_mode: str = "off",
    ) -> None:
        self._main = main
        self._council = council
        self._fallback = degraded_fallback
        self._reasoning_mode = reasoning_mode
        self.metrics_log: list[InferenceMetrics] = []

    # -- construction ------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        settings: Optional[Any] = None,
        *,
        degraded_fallback: Optional[InferencePort] = None,
    ) -> "TieredInferenceRouter":
        """Build both NIM clients from ``ATHENA_MAIN_/COUNCIL_`` settings.

        The coordinator wires this into ``app/api/dependencies.py`` when
        ``ATHENA_INFERENCE_MODE=live``; ``mock`` mode keeps ``MockInference``.
        """
        if settings is None:
            from app.athena.config import get_settings

            settings = get_settings()
        main = NimChatClient(
            NimClientConfig(
                base_url=settings.main_model_base_url,
                model_id=settings.main_model_id,
                api_key=settings.main_model_api_key,
                tier="main",
            )
        )
        council = NimChatClient(
            NimClientConfig(
                base_url=settings.council_model_base_url,
                model_id=settings.council_model_id,
                api_key=settings.council_model_api_key,
                tier="council",
                # The small model is cheap and slightly flakier under load; give
                # it one extra bounded retry so a transient 5xx/429 does not
                # zero a specialist finding (live reliability fix).
                max_retries=3,
                backoff_seconds=0.5,
            )
        )
        return cls(main=main, council=council, degraded_fallback=degraded_fallback)

    # -- routing -----------------------------------------------------------

    @staticmethod
    def route(role: str) -> RoutingRule:
        return ROUTING_MATRIX.get(role, _DEFAULT_RULE)

    def _client_for(self, rule: RoutingRule) -> InferencePort | NimChatClient:
        return self._main if rule.tier == "main" else self._council

    @property
    def last_metrics(self) -> Optional[InferenceMetrics]:
        return self.metrics_log[-1] if self.metrics_log else None

    def readiness(self) -> dict:
        def _h(c: object) -> dict:
            return c.health() if isinstance(c, NimChatClient) else {"component": "mock"}

        return {
            "component": "tiered_router",
            "reasoning_mode": self._reasoning_mode,
            "fallback_configured": self._fallback is not None,
            "main": _h(self._main),
            "council": _h(self._council),
        }

    # -- InferencePort -----------------------------------------------------

    async def complete(self, request: ChatRequest) -> ChatResult:
        rule = self.route(request.model_role)
        client = self._client_for(rule)

        try:
            raw = await self._call(client, request, rule)
            # Issue 2: a reasoning model (esp. Nemotron-Super) sometimes spends the
            # whole budget on hidden reasoning and returns an EMPTY answer channel.
            # This is orthogonal to schema validity, so it gets its own ONE retry
            # with a direct-answer nudge (reasoning is already toggled off) before
            # any schema repair. Applies to schema-less calls (plan/synthesis/
            # critic) too, so the orchestrator never receives empty content.
            empty_retried = False
            if not raw.content.strip():
                empty_retried = True
                raw = await self._call(
                    client, self._direct_answer_request(request), rule
                )
                if not raw.content.strip():
                    # Still empty: surface a degraded-but-non-empty result rather
                    # than an empty string the orchestrator would choke on.
                    raw.content = "{}"
                    raw.degraded = True
        except InferenceUnavailable:
            return await self._degraded_result(request, rule)

        repaired = False
        content = raw.content
        if request.response_schema is not None:
            obj = extract_json(content)
            if not _schema_ok(obj, request.response_schema):
                # Exactly ONE repair retry (AGENTS.md; FR5-08).
                repaired = True
                raw = await self._call(
                    client, self._repair_request(request, content), rule
                )
                obj = extract_json(raw.content)
                content = raw.content
                if _schema_ok(obj, request.response_schema):
                    content = _canonical_json(obj, fallback=raw.content)
                else:
                    raw.degraded = True
            else:
                # Return the clean, parsed JSON so downstream never re-parses noise.
                content = _canonical_json(obj, fallback=content)

        return self._finalize(request, rule, raw, content, repaired)

    # -- helpers -----------------------------------------------------------

    async def _call(
        self,
        client: InferencePort | NimChatClient,
        request: ChatRequest,
        rule: RoutingRule,
    ) -> NimResult:
        if isinstance(client, NimChatClient):
            return await client.complete(
                request.messages,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                timeout_seconds=request.timeout_seconds,
                reasoning_mode=self._reasoning_mode,
            )
        # A plain InferencePort (e.g. MockInference) — adapt its ChatResult.
        result = await client.complete(request)
        return NimResult(
            content=result.content,
            reasoning_content="",
            model_id=result.model_id,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            latency_ms=result.latency_ms,
            degraded=result.degraded,
            tier=rule.tier,
        )

    def _direct_answer_request(self, request: ChatRequest) -> ChatRequest:
        """Re-ask with an explicit no-reasoning nudge when the answer came back
        empty (all budget spent thinking). Reasoning is already toggled off at
        the client; this reinforces it in-band and enlarges the budget a little."""
        nudge = (
            "Do NOT think or deliberate. Output your final answer directly and "
            "immediately as the visible response now. If a JSON object was "
            "requested, output only that JSON object."
        )
        messages = list(request.messages) + [ChatMessage(role="user", content=nudge)]
        return ChatRequest(
            messages=messages,
            model_role=request.model_role,
            max_tokens=max(request.max_tokens, 1024),
            temperature=request.temperature,
            response_schema=request.response_schema,
            timeout_seconds=request.timeout_seconds,
        )

    def _repair_request(self, request: ChatRequest, bad: str) -> ChatRequest:
        required = _required_keys(request.response_schema)
        instruction = (
            "Your previous reply was not valid JSON for the required schema. "
            "Reply with a SINGLE valid JSON object ONLY — no prose, no markdown "
            "fences, no <think> block."
        )
        if required:
            instruction += " It MUST contain these keys: " + ", ".join(required) + "."
        messages = list(request.messages) + [
            ChatMessage(role="assistant", content=bad[:2000]),
            ChatMessage(role="user", content=instruction),
        ]
        return ChatRequest(
            messages=messages,
            model_role=request.model_role,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            response_schema=request.response_schema,
            timeout_seconds=request.timeout_seconds,
        )

    def _finalize(
        self,
        request: ChatRequest,
        rule: RoutingRule,
        raw: NimResult,
        content: str,
        repaired: bool,
    ) -> ChatResult:
        metrics = InferenceMetrics(
            model_id=raw.model_id,
            role=request.model_role,
            routing_reason=rule.reason,
            tier=raw.tier or rule.tier,
            prompt_tokens=raw.prompt_tokens,
            completion_tokens=raw.completion_tokens,
            ttft_ms=raw.ttft_ms,
            latency_ms=raw.latency_ms,
            degraded=raw.degraded,
            repaired=repaired,
            attempts=raw.attempts,
            finish_reason=raw.finish_reason,
            reasoning_tokens_estimate=len(raw.reasoning_content) // 4,
        )
        self.metrics_log.append(metrics)
        result = ChatResult(
            content=content,
            model_id=raw.model_id,
            role=request.model_role,
            prompt_tokens=raw.prompt_tokens,
            completion_tokens=raw.completion_tokens,
            latency_ms=raw.latency_ms,
            degraded=raw.degraded,
        )
        # Side-channel until core adds ttft_ms / routing_reason to ChatResult.
        result.metrics = metrics  # type: ignore[attr-defined]
        result.routing_reason = rule.reason  # type: ignore[attr-defined]
        result.ttft_ms = raw.ttft_ms  # type: ignore[attr-defined]
        return result

    async def _degraded_result(
        self, request: ChatRequest, rule: RoutingRule
    ) -> ChatResult:
        """Endpoint unavailable: use the deterministic fallback if wired, else
        return an explicitly degraded empty-JSON result (never crash the cycle).

        Degraded mode must not emit a high-risk recommendation without approval
        (FR4-06); the empty ``{}`` content carries no recommendation, and
        ``degraded=True`` signals the orchestrator to keep to restricted output.
        """
        if self._fallback is not None:
            fb = await self._fallback.complete(request)
            metrics = InferenceMetrics(
                model_id=fb.model_id,
                role=request.model_role,
                routing_reason=rule.reason + " | primary unavailable -> fallback",
                tier="fallback",
                prompt_tokens=fb.prompt_tokens,
                completion_tokens=fb.completion_tokens,
                latency_ms=fb.latency_ms,
                degraded=True,
            )
            self.metrics_log.append(metrics)
            fb.degraded = True
            fb.metrics = metrics  # type: ignore[attr-defined]
            fb.routing_reason = metrics.routing_reason  # type: ignore[attr-defined]
            fb.ttft_ms = None  # type: ignore[attr-defined]
            return fb

        metrics = InferenceMetrics(
            model_id="unavailable",
            role=request.model_role,
            routing_reason=rule.reason + " | endpoint unavailable -> degraded",
            tier=rule.tier,
            degraded=True,
            attempts=0,
        )
        self.metrics_log.append(metrics)
        result = ChatResult(
            content="{}",
            model_id="unavailable",
            role=request.model_role,
            degraded=True,
        )
        result.metrics = metrics  # type: ignore[attr-defined]
        result.routing_reason = metrics.routing_reason  # type: ignore[attr-defined]
        result.ttft_ms = None  # type: ignore[attr-defined]
        return result


# ---------------------------------------------------------------------------
# Lightweight schema helpers (no jsonschema dependency)
# ---------------------------------------------------------------------------


def _required_keys(schema: Optional[dict[str, Any]]) -> list[str]:
    if not schema:
        return []
    req = schema.get("required")
    if isinstance(req, list):
        return [str(k) for k in req]
    # Fall back to top-level property names if no explicit "required".
    props = schema.get("properties")
    if isinstance(props, dict):
        return list(props.keys())
    return []


def _schema_ok(obj: Optional[dict[str, Any]], schema: Optional[dict[str, Any]]) -> bool:
    if obj is None or not isinstance(obj, dict):
        return False
    for key in _required_keys(schema):
        if key not in obj:
            return False
    return True


def _canonical_json(obj: Optional[dict[str, Any]], *, fallback: str) -> str:
    import json

    if obj is None:
        return fallback
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
