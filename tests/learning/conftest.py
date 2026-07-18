"""Shared fixtures for the learning tests — fully offline / mock mode."""

from __future__ import annotations

import uuid

import pytest

from app.athena.schemas import (
    Cycle,
    CycleMetrics,
    Decision,
    Evidence,
    Finding,
    InvestigationPlan,
    MidCycleDecision,
    RoundBudget,
    StopConditions,
    TriggerKind,
)
from app.learning.service import LearningService
from app.learning.store import LearningStore
from app.security.contracts import (
    AgentEffect,
    FindingType,
    InspectionRequest,
    InspectionStage,
    SecurityAction,
    SecurityDecision,
    Severity,
)


# ---------------------------------------------------------------------------
# Deterministic mock gateways
# ---------------------------------------------------------------------------


class AllowGateway:
    """Allow-all gateway (the happy path)."""

    def __init__(self) -> None:
        self.calls: list[InspectionStage] = []

    def inspect(self, request: InspectionRequest) -> SecurityDecision:
        self.calls.append(request.stage)
        return SecurityDecision(
            action=SecurityAction.ALLOW,
            severity=Severity.INFO,
            confidence=1.0,
            finding=FindingType.NONE,
            agent_effect=AgentEffect.USED_IN_CONTEXT,
            safe_content=request.content,
            security_event_id="sec_allow_" + uuid.uuid4().hex[:6],
            rationale="mock-allow",
        )


class QuarantineGateway:
    """Quarantines any skill/runbook write; allows everything else."""

    def __init__(self, quarantine_stages=None) -> None:
        self.calls: list[InspectionStage] = []
        self._stages = quarantine_stages or {
            InspectionStage.SKILL_WRITE,
            InspectionStage.RUNBOOK_WRITE,
            InspectionStage.MEMORY_WRITE,
        }

    def inspect(self, request: InspectionRequest) -> SecurityDecision:
        self.calls.append(request.stage)
        if request.stage in self._stages:
            return SecurityDecision(
                action=SecurityAction.QUARANTINE,
                severity=Severity.CRITICAL,
                confidence=0.99,
                finding=FindingType.POISONED_ARTIFACT,
                agent_effect=AgentEffect.QUARANTINED,
                safe_content=None,
                security_incident_created=True,
                security_event_id="sec_quar_" + uuid.uuid4().hex[:6],
                rationale="mock-quarantine",
            )
        return SecurityDecision(
            action=SecurityAction.ALLOW,
            severity=Severity.INFO,
            confidence=1.0,
            safe_content=request.content,
        )


# ---------------------------------------------------------------------------
# Store / service fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    s = LearningStore(str(tmp_path / "athena.db"))
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def allow_gateway():
    return AllowGateway()


@pytest.fixture
def service(store, allow_gateway, tmp_path):
    return LearningService(
        store=store,
        security=allow_gateway,
        skills_dir=str(tmp_path / "skills"),
    )


# ---------------------------------------------------------------------------
# Cycle builders
# ---------------------------------------------------------------------------

REDIS_EVIDENCE_IDS = [
    "ev_metric-api-5xx-19",
    "ev_metric-redis-conn-20",
    "ev_diff-api-deploy-7",
]


def redis_evidence() -> list[Evidence]:
    return [
        Evidence(id="ev_metric-api-5xx-19", type="metric", source="prometheus",
                 data={"before": 0.01, "value": 0.18}),
        Evidence(id="ev_metric-redis-conn-20", type="metric", source="prometheus",
                 data={"before": 240, "value": 9900}),
        Evidence(id="ev_diff-api-deploy-7", type="diff", source="kubernetes",
                 data={"REDIS_POOL_MAX": {"from": "50", "to": "500"}}),
    ]


def make_redis_cycle(
    *,
    revise: bool = True,
    confidence: float = 0.86,
    evidence_ids: list[str] | None = None,
    verified_decision: Decision = Decision.ESCALATE,
) -> Cycle:
    """A verified Redis incident-one cycle with a REVISE_PLAN mid-cycle decision."""
    ids = evidence_ids if evidence_ids is not None else list(REDIS_EVIDENCE_IDS)
    plan = InvestigationPlan(
        plan_id="plan_" + uuid.uuid4().hex[:8],
        incident_ref="redis-incident-001",
        max_specialists=2,
        max_tool_calls=10,
        max_tokens=14000,
        deadline_seconds=75,
        first_round=RoundBudget(specialists=["observability"], max_tokens=3000),
        second_round=RoundBudget(specialists=["platform"], max_tokens=3000),
        stop_conditions=StopConditions(),
        platform_question="What infrastructure change explains the symptom?",
    )
    if revise:
        plan.revise_platform_question(
            "Which Redis connection-pool change created the exhaustion?",
            reason=(
                "Round-1 Observability evidence shows Redis rejected connections "
                "climbing before API 5xx — errors originate at the Redis dependency, "
                "not inside the calling service."
            ),
        )

    obs = Finding(
        id="find_obs", role="observability",
        hypothesis="API 5xx rise correlates with Redis rejected-connection growth.",
        confidence=0.6, cited_evidence_ids=ids[:2],
    )
    platform = Finding(
        id="find_plat", role="platform",
        hypothesis="Deployment raised REDIS_POOL_MAX, saturating the connection pool.",
        confidence=0.72, cited_evidence_ids=ids,
    )
    synthesis = Finding(
        id="find_synth", role="synthesis",
        hypothesis="Redis connection pool saturation from a deployment-induced pool-size change raised 5xx.",
        interpretation="Deployment diff and metrics jointly support the Redis connection-saturation root cause.",
        confidence=confidence, cited_evidence_ids=ids,
    )

    return Cycle(
        cycle_id="cycle_" + uuid.uuid4().hex[:12],
        trigger=TriggerKind.manual,
        fixture="redis-incident-001",
        decision=verified_decision,
        signals_checked=["metrics", "kubernetes_events"],
        changes_detected=[{"source": "kubernetes", "kind": "deployment", "service": "api"}],
        plan=plan,
        mid_cycle_decision=(
            MidCycleDecision.REVISE_PLAN if revise else MidCycleDecision.CONTINUE_PLAN
        ),
        evidence_ids=ids,
        findings=[obs, platform, synthesis],
        metrics=CycleMetrics(model_calls=6, tool_calls=2, specialists_invoked=2, total_tokens=8000),
    )
