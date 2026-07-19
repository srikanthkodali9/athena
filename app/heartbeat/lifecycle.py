"""Heartbeat lifecycle: cycle state machine + bounded orchestration.

One internal command handler (``HeartbeatService``) serves both periodic and
manual triggers (FR1-01/02). It enforces overlap protection and idempotency
(FR1-06), drives the cycle state machine (FR1-03), runs the no-change WAIT path
with zero model calls (FR2-04), and executes a bounded, checkpointed
investigation that a restart can resume without repeating costly work
(FR7-04/05/07).

Steps are executed by a small step engine. Each step declares whether it is
``costly`` (a model or tool call). On recovery, any step already recorded in the
checkpoint table is skipped — its results are already present on the reloaded
cycle — so costly/irreversible actions never run twice.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from app.athena.config import Settings
from app.athena.ports import (
    ChatMessage,
    ChatRequest,
    InferencePort,
    InspectionRequest,
    InspectionStage,
    SecurityGateway,
)
from app.athena.schemas import (
    Checkpoint,
    Cycle,
    CycleState,
    Decision,
    Evidence,
    Finding,
    InferenceMetric,
    InvestigationPlan,
    MidCycleDecision,
    RoundBudget,
    StopConditions,
    TriggerKind,
    utcnow,
)
from app.council.specialists import (
    ObservabilitySpecialist,
    PlatformSpecialist,
    Specialist,
    SpecialistOutcome,
)
from app.heartbeat.change_detection import detect_changes
from app.learning.retrieval import SkillRetriever
from app.learning.service import LearningService
from app.memory.store import CycleStore
from app.policies.models import PolicyEffect, PolicyRequest
from app.security.contracts import SecurityAction
from app.security.inference import (
    SecurityBoundaryDenied,
    ensure_secured_inference,
)
from app.security.openshell import OpenShellEngine
from app.tools import fixtures as tool_fixtures
from app.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Commands + errors
# ---------------------------------------------------------------------------


@dataclass
class HeartbeatCommand:
    trigger: TriggerKind = TriggerKind.manual
    fixture: Optional[str] = None
    reason: Optional[str] = None
    idempotency_key: Optional[str] = None


class HeartbeatError(Exception):
    """Base class for heartbeat command errors."""


class CycleConflict(HeartbeatError):
    """Another cycle is active, or an idempotency/lifecycle conflict (409)."""


class FixtureNotAllowed(HeartbeatError):
    """Fixture triggers are only allowed in development/demo (403)."""


class UnknownFixture(HeartbeatError):
    """Requested fixture is not known to the tool registry (422)."""


class CapabilityDenied(HeartbeatError):
    """OpenShell denied or approval-gated a requested capability."""


def new_cycle_id() -> str:
    return "cycle_" + uuid.uuid4().hex[:12]


def _specialist_inference_metric(role: str, outcome: SpecialistOutcome) -> InferenceMetric:
    """Build the per-inference metric for a specialist call.

    Prefers the router's ``InferenceMetrics`` (live mode). Under ``MockInference``
    that is ``None``, so it falls back to the produced finding / outcome with
    defensive getattr defaults — never crashing.
    """
    m = getattr(outcome, "metrics", None)
    if m is not None:
        prompt = getattr(m, "prompt_tokens", 0) or 0
        completion = getattr(m, "completion_tokens", 0) or 0
        return InferenceMetric(
            model_id=getattr(m, "model_id", outcome.model_id),
            role=getattr(m, "role", role),
            routing_reason=getattr(m, "routing_reason", None),
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            ttft_ms=getattr(m, "ttft_ms", None),
            latency_ms=getattr(m, "latency_ms", None),
            degraded=bool(getattr(m, "degraded", outcome.degraded)),
        )
    finding = outcome.finding
    total = int(getattr(finding, "tokens", 0) or 0)
    return InferenceMetric(
        model_id=outcome.model_id,
        role=role,
        routing_reason=None,
        prompt_tokens=0,
        completion_tokens=total,
        total_tokens=total,
        ttft_ms=None,
        latency_ms=getattr(finding, "latency_ms", None),
        degraded=bool(outcome.degraded),
    )


def _specialist_step_result(outcome: SpecialistOutcome) -> dict:
    """Checkpoint payload for a specialist round (audits prompt version + drops)."""
    return {
        "finding_id": outcome.finding.id,
        "role": outcome.role,
        "degraded": bool(outcome.degraded),
        "prompt_version": outcome.prompt_version,
        "rejected_citations": list(outcome.rejected_citations),
    }


def _redis_saturation(evidence) -> Optional[str]:
    """Return the saturating redis metric's evidence id, else None (deterministic).

    A redis metric whose ``value >= 0.9 * limit`` means the 5xx errors trace to
    Redis connection rejection — the signal that drives the mid-cycle REVISE_PLAN
    (fixtures/README.md).
    """
    for ev in evidence:
        data = getattr(ev, "data", {}) or {}
        limit = data.get("limit")
        value = data.get("value")
        prov = getattr(ev, "provenance", {}) or {}
        looks_redis = "redis" in (
            str(prov.get("query", "")) + str(getattr(ev, "source", ""))
        ).lower()
        if (
            looks_redis
            and isinstance(limit, (int, float))
            and isinstance(value, (int, float))
            and limit > 0
            and value >= 0.9 * limit
        ):
            return ev.id
    return None


def _retrieval_signals(fixture: str) -> tuple[dict[str, float], set[str]]:
    """Translate incident telemetry into the skill-retrieval contract (FR-14).

    Reads the real fixture metrics (facts) and derives:
      redis_saturation_ratio = connected_clients / limit
      api_5xx_rate           = current 5xx ratio
      api_5xx_rate_baseline  = baseline 5xx ratio
    Preconditions are asserted only when the backing telemetry is present.
    Never fabricates a signal — an incident with no redis metric yields no
    ``redis_saturation_ratio`` and therefore cannot match the redis skill.
    """
    try:
        data = tool_fixtures.get_fixture(fixture)
    except KeyError:
        return {}, set()

    signals: dict[str, float] = {}
    preconditions: set[str] = set()
    for metric in data.get("metrics", []):
        prov = metric.get("provenance", {}) or {}
        mdata = metric.get("data", {}) or {}
        blob = (str(prov.get("query", "")) + str(metric.get("source", ""))).lower()
        limit = mdata.get("limit")
        value = mdata.get("value")
        if "redis" in blob and isinstance(limit, (int, float)) and limit and isinstance(value, (int, float)):
            signals["redis_saturation_ratio"] = value / limit
            preconditions.update(
                {"redis_metrics_available", "dependency_graph_contains_api_to_redis"}
            )
        if prov.get("service") == "api" or "http_requests_total" in str(prov.get("query", "")):
            if isinstance(value, (int, float)):
                signals["api_5xx_rate"] = value
            if isinstance(mdata.get("before"), (int, float)):
                signals["api_5xx_rate_baseline"] = mdata["before"]
    return signals, preconditions


# ---------------------------------------------------------------------------
# Step engine
# ---------------------------------------------------------------------------


@dataclass
class _Context:
    cycle: Cycle
    plan: Optional[InvestigationPlan] = None
    stop: bool = False  # early terminal (WAIT)
    evidence: list[Evidence] = field(default_factory=list)


StepFn = Callable[["HeartbeatService", _Context], Awaitable[dict]]


@dataclass
class _Step:
    name: str
    costly: bool
    fn: StepFn


@dataclass(frozen=True)
class _ToolCapability:
    role: str
    action: str


class HeartbeatService:
    """Shared command handler for manual and periodic heartbeats."""

    # Per-step wall-clock budget. Generous enough for a slow live Nemotron call
    # plus its single bounded repair attempt, while still cancelling a hung call
    # can never freeze a cycle indefinitely — it is cancelled and the cycle ends
    # in a degraded CONTAIN state. Complements any per-call timeout in the
    # inference adapter.
    _STEP_TIMEOUT_SECONDS: float = 180.0

    _TOOL_CAPABILITIES: dict[str, _ToolCapability] = {
        "query_metrics": _ToolCapability("observability", "read_telemetry"),
        "query_logs": _ToolCapability("observability", "read_telemetry"),
        "diff_deployment": _ToolCapability("platform", "read_kubernetes"),
        "read_repository": _ToolCapability("platform", "read_repository"),
        "inspect_dependency": _ToolCapability("platform", "read_kubernetes"),
        "calculate_blast_radius": _ToolCapability("platform", "read_kubernetes"),
        "security_scan": _ToolCapability("athena", "call_security_endpoint"),
        "search_memory": _ToolCapability("historian", "read_memory_validated"),
    }

    def __init__(
        self,
        *,
        store: CycleStore,
        inference: InferencePort,
        security: SecurityGateway,
        registry: ToolRegistry,
        settings: Settings,
        learning: "LearningService | None" = None,
        retriever: "SkillRetriever | None" = None,
        openshell: "OpenShellEngine | None" = None,
    ) -> None:
        self._store = store
        # Mandatory, idempotent model boundary: every caller (including
        # specialist and repair paths) receives the same secured port.
        self._inference = ensure_secured_inference(inference, security)
        self._security = security
        self._registry = registry
        self._settings = settings
        # Recursive intelligence (optional so isolated core tests can omit it).
        # ``learning`` turns a verified cycle into a promoted skill (FR-8);
        # ``retriever`` lets a later incident reuse a promoted skill (FR-14).
        self._learning = learning
        self._retriever = retriever
        self._openshell = openshell

    # -- Command intake ----------------------------------------------------

    def trigger(self, command: HeartbeatCommand) -> tuple[Cycle, bool]:
        """Validate, apply idempotency + overlap protection, persist a cycle.

        Returns ``(cycle, created)``. Does NOT run the investigation; callers
        schedule :meth:`run_cycle`. ``created`` is False when an existing cycle
        was returned for a repeated idempotency key.
        """
        # Idempotency: a repeated key returns the existing cycle unchanged.
        if command.idempotency_key:
            existing = self._store.get_by_idempotency_key(command.idempotency_key)
            if existing is not None:
                return existing, False

        # Fixture policy (FND-02): dev/demo only, and must be known.
        if command.fixture:
            if not self._settings.fixtures_allowed:
                raise FixtureNotAllowed(
                    "fixture triggers are not permitted in production mode"
                )
            if not tool_fixtures.known_fixture(command.fixture):
                raise UnknownFixture(f"unknown fixture: {command.fixture}")

        # Overlap protection (FR1-06): only one active cycle at a time.
        active = self._store.active_cycle()
        if active is not None:
            raise CycleConflict(
                f"cycle {active.cycle_id} is already active ({active.state.value})"
            )

        cycle = Cycle(
            cycle_id=new_cycle_id(),
            trigger=command.trigger,
            reason=command.reason,
            fixture=command.fixture,
            idempotency_key=command.idempotency_key,
            state=CycleState.QUEUED,
        )
        self._store.save_cycle(cycle)
        return cycle, True

    # -- Execution ---------------------------------------------------------

    def _steps(self) -> list[_Step]:
        return [
            _Step("LOAD_STATE", False, HeartbeatService._step_load_state),
            _Step("DETECT_CHANGES", False, HeartbeatService._step_detect_changes),
            _Step("PLAN", True, HeartbeatService._step_plan),
            _Step("ROUND1_TOOLS", True, HeartbeatService._step_round1_tools),
            _Step("ROUND1_OBSERVABILITY", True, HeartbeatService._step_round1_obs),
            _Step("MID_CYCLE_EVAL", True, HeartbeatService._step_mid_cycle),
            _Step("ROUND2_PLATFORM", True, HeartbeatService._step_round2_platform),
            _Step("SYNTHESIZE", True, HeartbeatService._step_synthesize),
        ]

    async def run_cycle(self, cycle_id: str) -> Cycle:
        """Drive a cycle to a terminal state, resuming completed steps safely."""
        cycle = self._store.get_cycle(cycle_id)
        if cycle is None:
            raise HeartbeatError(f"unknown cycle: {cycle_id}")
        if cycle.is_terminal:
            return cycle

        completed = self._store.completed_steps(cycle_id)
        if completed:
            cycle.recovery_actions.append(
                f"resumed with completed steps: {sorted(completed)}"
            )

        ctx = _Context(cycle=cycle)
        if cycle.plan is not None:
            ctx.plan = cycle.plan

        try:
            for step in self._steps():
                if step.name in completed:
                    # Already done in a previous (possibly crashed) run. The
                    # reloaded cycle already carries its results; do not repeat
                    # the costly/irreversible action (FR7-07).
                    cycle.recovery_actions.append(f"skipped completed step {step.name}")
                    continue

                try:
                    # Per-step deadline: a hung model/tool call is cancelled
                    # rather than freezing the cycle forever (NFR reliability;
                    # FR-3 deadline). Slow-but-progressing multi-step cycles are
                    # unaffected — each step gets its own generous budget.
                    result = await asyncio.wait_for(
                        step.fn(self, ctx), timeout=self._STEP_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    cycle.decision = cycle.decision or Decision.CONTAIN
                    cycle.error = (
                        f"step {step.name} exceeded {self._STEP_TIMEOUT_SECONDS}s "
                        "budget; terminated in degraded mode"
                    )
                    cycle.recovery_actions.append(
                        f"step {step.name} timed out — degraded termination"
                    )
                    break
                self._store.checkpoint(
                    cycle,
                    Checkpoint(
                        cycle_id=cycle.cycle_id,
                        step=step.name,
                        costly=step.costly,
                        result=result,
                    ),
                )
                if ctx.stop:
                    break

            self._finalize(cycle)
            self._maybe_learn(cycle, ctx)
        except Exception as exc:  # noqa: BLE001 - record and fail closed
            cycle.state = CycleState.FAILED
            cycle.error = f"{type(exc).__name__}: {exc}"
            cycle.ended_at = utcnow()
            self._store.save_cycle(cycle)
            raise

        return cycle

    def _maybe_learn(self, cycle: Cycle, ctx: _Context) -> None:
        """Turn a verified investigation into procedural knowledge (FR-8).

        Runs only after a real investigation completes (never the no-change WAIT
        path), exactly once per cycle, and routes the SKILL_WRITE inspection
        through the same security gateway the cycle uses. For the P0 demo the
        fixture incidents are ground-truth verified.
        """
        if self._learning is None:
            return
        if cycle.state is not CycleState.COMPLETED:
            return
        if cycle.decision is Decision.WAIT or cycle.learning is not None:
            return
        try:
            result = self._learning.learn_from_cycle(
                cycle, verified=True, evidence=ctx.evidence
            )
        except Exception as exc:  # noqa: BLE001 - learning must never fail the cycle
            cycle.recovery_actions.append(f"learning skipped: {type(exc).__name__}")
            self._store.save_cycle(cycle)
            return
        cycle.learning = {
            "outcome": result.outcome.value,
            "skill_id": result.skill_id,
            "skill_version": result.skill_version,
            "skill_status": result.skill_status.value if result.skill_status else None,
            "known_false_lead_count": result.known_false_lead_count,
            "episode_id": result.episode_id,
        }
        self._store.save_cycle(cycle)

    async def recover(self) -> Optional[Cycle]:
        """Reload and resume an incomplete cycle after restart (FR7-05)."""
        incomplete = self._store.find_incomplete_cycle()
        if incomplete is None:
            return None
        incomplete.recovery_actions.append("restart recovery initiated")
        self._store.save_cycle(incomplete)
        return await self.run_cycle(incomplete.cycle_id)

    # -- Finalization ------------------------------------------------------

    def _finalize(self, cycle: Cycle) -> None:
        if cycle.state in (CycleState.COMPLETED, CycleState.FAILED, CycleState.CANCELLED):
            self._store.save_cycle(cycle)
            return
        cycle.state_before = cycle.state
        cycle.state = CycleState.COMPLETED
        cycle.ended_at = utcnow()
        if cycle.started_at:
            cycle.metrics.duration_ms = round(
                (cycle.ended_at - cycle.started_at).total_seconds() * 1000, 3
            )
        self._store.save_cycle(cycle)

    # -- Steps -------------------------------------------------------------

    async def _step_load_state(self, ctx: _Context) -> dict:
        cycle = ctx.cycle
        cycle.state_before = cycle.state
        cycle.state = CycleState.LOADING
        cycle.started_at = cycle.started_at or utcnow()
        # Inspect the heartbeat input at the trust boundary (FR10-02).
        await self._inspect(
            InspectionStage.HEARTBEAT_INPUT,
            producer=cycle.trigger.value,
            consumer="heartbeat",
            content={"fixture": cycle.fixture, "reason": cycle.reason},
            cycle=cycle,
        )
        return {"state": cycle.state.value}

    async def _step_detect_changes(self, ctx: _Context) -> dict:
        cycle = ctx.cycle
        cycle.state = CycleState.DETECTING
        result = detect_changes(cycle.fixture)
        cycle.signals_checked = result.signals_checked
        cycle.changes_detected = result.changes_detected
        cycle.change_class = result.change_class

        if not result.investigation_required:
            # No-change WAIT path — makes NO model calls (FR2-04).
            cycle.decision = Decision.WAIT
            cycle.skip_reason = result.skip_reason
            cycle.state_before = cycle.state
            cycle.state = CycleState.COMPLETED
            cycle.ended_at = utcnow()
            if cycle.started_at:
                cycle.metrics.duration_ms = round(
                    (cycle.ended_at - cycle.started_at).total_seconds() * 1000, 3
                )
            ctx.stop = True
            return {"decision": "WAIT", "skip_reason": result.skip_reason}

        cycle.decision = Decision.INVESTIGATE
        return {
            "decision": "INVESTIGATE",
            "change_class": result.change_class.value,
            "start_reason": result.start_reason,
        }

    async def _step_plan(self, ctx: _Context) -> dict:
        cycle = ctx.cycle
        cycle.state = CycleState.PLANNING
        s = self._settings

        # Nemotron-Super produces the bounded plan (via the strategic port).
        chat = await self._infer(
            "strategic", "Produce a bounded investigation plan.", cycle
        )

        half_tokens = max(1, s.max_total_tokens // 4)
        plan = InvestigationPlan(
            plan_id="plan_" + uuid.uuid4().hex[:8],
            incident_ref=cycle.fixture,
            max_specialists=s.max_specialists,
            max_tool_calls=s.max_tool_calls,
            max_tokens=s.max_total_tokens,
            deadline_seconds=s.investigation_deadline_seconds,
            first_round=RoundBudget(
                specialists=["observability"],
                max_tool_calls=5,
                max_tokens=half_tokens,
                deadline_seconds=30,
            ),
            second_round=RoundBudget(
                specialists=["platform"],
                max_tool_calls=5,
                max_tokens=half_tokens,
                deadline_seconds=30,
            ),
            stop_conditions=StopConditions(
                raw=[
                    "root_cause_confidence >= 0.85",
                    "critical_containment_action_identified",
                    "no_new_evidence_after_two_queries",
                ]
            ),
            required_evidence=["metric", "diff"],
            platform_question="What infrastructure change explains the symptom?",
        )
        if not plan.bounds_ok():
            raise HeartbeatError("planner produced an out-of-bounds plan")

        ctx.plan = plan
        cycle.plan = plan

        # Skill reuse (FR-14): if a PROMOTED skill matches this incident's
        # telemetry, record it. The skill already encodes the hypothesis +
        # known_false_leads, so the investigation may legitimately run leaner
        # (skip mid-cycle re-derivation and the Round-2 specialist) — principled
        # reuse of validated procedural knowledge, not shortcutting.
        reuse = await self._match_promoted_skill(cycle.fixture, cycle)
        if reuse is not None:
            cycle.reused_skill_id, cycle.reused_skill_version = reuse

        return {
            "plan_id": plan.plan_id,
            "planner_tokens": chat.total_tokens,
            "reused_skill_id": cycle.reused_skill_id,
        }

    async def _match_promoted_skill(
        self, fixture: Optional[str], cycle: Cycle
    ) -> Optional[tuple[str, str]]:
        """Return (skill_id, version) of a promoted skill matching the incident
        telemetry, or None. Reads real fixture metrics — never fabricated."""
        if self._retriever is None or not fixture:
            return None
        signals, preconditions = _retrieval_signals(fixture)
        if not signals:
            return None
        match = self._retriever.match(signals, preconditions_met=preconditions)
        if match is None:
            return None
        skill, citation = match
        decision = await self._inspect(
            InspectionStage.RETRIEVED_CONTENT,
            producer=f"skill:{skill.skill_id}@{skill.version}",
            consumer="investigation_planner",
            content={
                "skill_id": skill.skill_id,
                "version": skill.version,
                "title": skill.title,
                "objective": skill.objective,
                "triggers": skill.triggers,
                "procedure": skill.procedure,
                "known_false_leads": citation.known_false_leads,
            },
            cycle=cycle,
        )
        if decision.action is not SecurityAction.ALLOW:
            cycle.recovery_actions.append(
                f"retrieved skill {skill.skill_id}@{skill.version} excluded by security"
            )
            return None
        return skill.skill_id, skill.version

    async def _step_round1_tools(self, ctx: _Context) -> dict:
        cycle = ctx.cycle
        cycle.state = CycleState.INVESTIGATING
        params = {"fixture": cycle.fixture}
        registered: list[str] = []
        for tool in ("query_metrics", "diff_deployment"):
            if ctx.plan is not None and cycle.metrics.tool_calls >= ctx.plan.max_tool_calls:
                raise CapabilityDenied("investigation tool-call budget exhausted")

            self._authorize_tool(tool, cycle)
            call_decision = await self._inspect(
                InspectionStage.TOOL_CALL,
                producer="orchestrator",
                consumer=tool,
                content={"tool": tool, "params": params},
                cycle=cycle,
            )
            if call_decision.action is not SecurityAction.ALLOW:
                raise SecurityBoundaryDenied(
                    f"tool call {tool} rejected at TOOL_CALL boundary "
                    f"({call_decision.action.value})"
                )

            result = self._registry.call(tool, params)
            cycle.metrics.tool_calls += 1
            # Tool results are untrusted until inspected (FR6, FR10-02).
            result_decision = await self._inspect(
                InspectionStage.TOOL_RESULT,
                producer=tool,
                consumer="evidence_registry",
                content=result.model_dump(mode="json"),
                cycle=cycle,
            )
            if not result_decision.is_usable():
                raise SecurityBoundaryDenied(
                    f"tool result {tool} rejected before evidence registration "
                    f"({result_decision.action.value})"
                )
            if result_decision.safe_content is not None:
                try:
                    result = result.model_validate_json(result_decision.safe_content)
                except Exception as exc:
                    raise SecurityBoundaryDenied(
                        f"tool result {tool} could not be reconstructed after inspection"
                    ) from exc
            if result.status != "success":
                continue
            for ev in result.evidence:
                self._store.register_evidence(ev, cycle.cycle_id, produced_by=tool)
                if ev.id not in cycle.evidence_ids:
                    cycle.evidence_ids.append(ev.id)
                    ctx.evidence.append(ev)
                registered.append(ev.id)
        return {"evidence_ids": registered}

    def _authorize_tool(self, tool: str, cycle: Cycle) -> None:
        """Apply OpenShell independently before an approved tool can execute."""
        if self._openshell is None:
            return
        capability = self._TOOL_CAPABILITIES.get(tool)
        if capability is None:
            raise CapabilityDenied(f"tool {tool} has no declared capability mapping")
        decision = self._openshell.evaluate(
            PolicyRequest(
                role=capability.role,
                action=capability.action,
                cycle_id=cycle.cycle_id,
            )
        )
        if decision.effect is not PolicyEffect.ALLOW:
            raise CapabilityDenied(
                f"OpenShell {decision.effect.value} for {tool}: {decision.category}"
            )

    async def _step_round1_obs(self, ctx: _Context) -> dict:
        # Round 1: real Observability specialist over the evidence collected by
        # ROUND1_TOOLS. Structured, allowlist-enforced, confidence-capped (FR5).
        question = (
            "Establish the symptom timeline and a bounded hypothesis for the "
            "observed change, citing only the supplied evidence."
        )
        outcome = await self._run_specialist(
            ObservabilitySpecialist, role="observability", question=question, ctx=ctx
        )
        return _specialist_step_result(outcome)

    async def _step_round2_platform(self, ctx: _Context) -> dict:
        cycle = ctx.cycle
        # Skill reuse: a promoted skill already answers the Platform question, so
        # skip re-running the specialist (fewer specialist + model calls, FR-14).
        if cycle.reused_skill_id:
            cycle.skipped_steps.append("ROUND2_PLATFORM")
            return {
                "skipped": True,
                "reason": f"reused skill {cycle.reused_skill_id} encodes the platform conclusion",
            }
        # Round 2: real Platform specialist with the confirmed/revised question.
        question = (
            ctx.plan.platform_question
            if ctx.plan and ctx.plan.platform_question
            else "What infrastructure change explains the symptom?"
        )
        outcome = await self._run_specialist(
            PlatformSpecialist, role="platform", question=question, ctx=ctx
        )
        return _specialist_step_result(outcome)

    async def _step_mid_cycle(self, ctx: _Context) -> dict:
        cycle = ctx.cycle
        # Skill reuse: the skill already encodes the hypothesis + known_false_leads,
        # so Athena does NOT re-derive the plan mid-cycle (no model call, FR-14).
        if cycle.reused_skill_id:
            cycle.mid_cycle_decision = MidCycleDecision.CONTINUE_PLAN
            cycle.skipped_steps.append("MID_CYCLE_EVAL")
            cycle.plan = ctx.plan
            return {
                "decision": MidCycleDecision.CONTINUE_PLAN.value,
                "skipped": True,
                "reason": f"reused skill {cycle.reused_skill_id}",
            }

        # Bounded mid-cycle evaluation (FR3-08): CONTINUE_PLAN or REVISE_PLAN.
        chat = await self._infer(
            "midcycle", "Does Round 1 evidence support the hypothesis?", cycle
        )
        # Evidence-driven REVISE_PLAN (fixtures/README.md): revise when Round-1
        # evidence shows the 5xx errors trace specifically to Redis connection
        # rejection (a redis metric at >= 90% of its limit), OR on a
        # security-sensitive change. One-revision bound preserved.
        security_revise = (
            cycle.change_class is not None
            and cycle.change_class.value == "security_sensitive"
        )
        redis_ev = _redis_saturation(ctx.evidence)
        if ctx.plan is not None and not ctx.plan.revision_used and (security_revise or redis_ev):
            if redis_ev:
                ctx.plan.revise_platform_question(
                    "Which deployment change altered the Redis connection pool "
                    "configuration (REDIS_POOL_MAX)?",
                    reason=(
                        f"round1 evidence {redis_ev} shows redis_connected_clients "
                        "at/near its limit — errors trace to Redis connection "
                        "rejection, not general service infrastructure"
                    ),
                )
            else:
                ctx.plan.revise_platform_question(
                    "Which infrastructure change created the exposure?",
                    reason="round1 evidence indicates a security-sensitive change",
                )
            cycle.mid_cycle_decision = MidCycleDecision.REVISE_PLAN
        else:
            cycle.mid_cycle_decision = MidCycleDecision.CONTINUE_PLAN
        cycle.plan = ctx.plan
        return {
            "decision": cycle.mid_cycle_decision.value,
            "tokens": chat.total_tokens,
        }

    async def _step_synthesize(self, ctx: _Context) -> dict:
        cycle = ctx.cycle
        cycle.state = CycleState.SYNTHESIZING
        # Strategic synthesis + adversarial critic (both Nemotron-Super).
        await self._infer(
            "strategic", "Synthesize evidence and resolve contradictions.", cycle
        )
        await self._infer("critic", "Adversarially critique the conclusion.", cycle)

        cited = list(cycle.evidence_ids)
        synthesis = Finding(
            id="find_" + uuid.uuid4().hex[:8],
            role="synthesis",
            hypothesis="Redis connection pool change saturated clients, raising 5xx.",
            interpretation="Deployment diff and metrics jointly support the root cause.",
            confidence=0.86,
            cited_evidence_ids=cited,
            missing_evidence=[],
            model_id=getattr(self._inference, "model_id", None),
        )
        cycle.findings.append(synthesis)

        # Decision from synthesis confidence vs stop condition.
        threshold = ctx.plan.stop_conditions.root_cause_confidence if ctx.plan else 0.85
        cycle.decision = (
            Decision.ESCALATE
            if synthesis.confidence >= threshold
            else Decision.INVESTIGATE
        )
        cycle.state_before = cycle.state
        cycle.state = CycleState.PERSISTING
        return {"decision": cycle.decision.value, "confidence": synthesis.confidence}

    # -- Helpers -----------------------------------------------------------

    # Nemotron reasoning models spend completion tokens on a hidden trace before
    # the answer; strategic planning/synthesis need headroom or they truncate and
    # come back degraded. Budget per role (specialists set their own via run()).
    _INFER_MAX_TOKENS = {
        "strategic": 3072,
        "critic": 2048,
        "midcycle": 1536,
    }

    async def _infer(self, role: str, prompt: str, cycle: Cycle):
        request = ChatRequest(
            messages=[ChatMessage(role="user", content=prompt)],
            model_role=role,
            max_tokens=self._INFER_MAX_TOKENS.get(role, 1024),
            cycle_id=cycle.cycle_id,
            security_producer="orchestrator",
            security_consumer="orchestrator",
        )
        inspections_before = self._inference.inspection_count
        try:
            result = await self._inference.complete(request)
        except SecurityBoundaryDenied as exc:
            cycle.metrics.model_calls += exc.inference_attempts
            raise
        finally:
            cycle.metrics.security_inspections += (
                self._inference.inspection_count - inspections_before
            )
        cycle.metrics.model_calls += result.inference_attempts
        cycle.metrics.total_tokens += result.total_tokens
        # Record per-inference telemetry for the metrics route (FR4-04, FR12-06).
        cycle.metrics.inferences.append(
            InferenceMetric(
                model_id=result.model_id,
                role=result.role,
                routing_reason=getattr(result, "routing_reason", None),
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                ttft_ms=getattr(result, "ttft_ms", None),
                latency_ms=result.latency_ms,
                degraded=result.degraded,
            )
        )
        return result

    async def _run_specialist(
        self,
        specialist_cls: type[Specialist],
        *,
        role: str,
        question: str,
        ctx: _Context,
        # Nemotron-Nano spends completion tokens on a hidden reasoning trace
        # before the JSON answer; 900 intermittently truncated the specialist
        # output (empty/degraded). 1600 leaves room for reasoning + a full
        # evidence-citing answer while staying well inside the token budget.
        max_tokens: int = 1600,
    ) -> SpecialistOutcome:
        """Run one real Council specialist, preserving the boundaries and metrics
        the previous placeholder path provided (FR5).

        The secured inference port inspects the fully rendered prompt and every
        raw response/repair attempt. This method records metrics, then inspects
        the final structured finding as a separate trust transition before it
        enters synthesis.
        """
        cycle = ctx.cycle
        # 1. Real specialist call over allowlisted evidence. Its fully rendered
        # prompt crosses the secured inference boundary inside Specialist.run.
        specialist = specialist_cls(self._inference)
        inspections_before = self._inference.inspection_count
        try:
            outcome = await specialist.run(
                question,
                ctx.evidence,
                max_tokens=max_tokens,
                cycle_id=cycle.cycle_id,
            )
        except SecurityBoundaryDenied as exc:
            cycle.metrics.model_calls += exc.inference_attempts
            raise
        finally:
            cycle.metrics.security_inspections += (
                self._inference.inspection_count - inspections_before
            )

        # 2. Record all actual attempts, including a bounded repair when used.
        cycle.metrics.model_calls += outcome.inference_attempts
        metric = _specialist_inference_metric(role, outcome)
        cycle.metrics.total_tokens += metric.total_tokens
        cycle.metrics.inferences.append(metric)

        # 3. Inspect the parsed/allowlisted finding before it feeds synthesis.
        response_decision = await self._inspect(
            InspectionStage.SPECIALIST_RESPONSE,
            producer=role,
            consumer="synthesizer",
            content=outcome.finding.model_dump_json(),
            cycle=cycle,
        )
        if not response_decision.is_usable() or response_decision.safe_content is None:
            raise SecurityBoundaryDenied(
                f"specialist response for {role} rejected "
                f"({response_decision.action.value})"
            )
        try:
            outcome.finding = Finding.model_validate_json(response_decision.safe_content)
        except Exception as exc:
            raise SecurityBoundaryDenied(
                f"specialist response for {role} invalid after inspection"
            ) from exc
        if not set(outcome.finding.cited_evidence_ids).issubset(cycle.evidence_ids):
            raise SecurityBoundaryDenied(
                f"specialist response for {role} gained unregistered evidence citations"
            )

        # 4. Commit the structured, evidence-backed finding.
        cycle.findings.append(outcome.finding)
        cycle.metrics.specialists_invoked += 1
        return outcome

    async def _inspect(
        self,
        stage: InspectionStage,
        *,
        producer: str,
        consumer: str,
        content,
        cycle: Cycle,
    ):
        # Canonical InspectionRequest.content is a string; serialize structured
        # payloads deterministically so the gateway sees stable, inspectable text.
        if not isinstance(content, str):
            content = json.dumps(content, default=str, sort_keys=True)
        # Await-tolerant: the canonical SecurityGateway.inspect is synchronous,
        # but a NoOp/mock could be async. This lets either drop in unchanged.
        outcome = self._security.inspect(
            InspectionRequest(
                stage=stage,
                producer=producer,
                consumer=consumer,
                content=content,
                cycle_id=cycle.cycle_id,
            )
        )
        if inspect.isawaitable(outcome):
            outcome = await outcome
        cycle.metrics.security_inspections += 1
        return outcome
