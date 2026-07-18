# Demo fixtures (demo_engineer owned)

Deterministic, synthetic, timestamped fixtures for the six-minute Athena demo
(PRD §21–§22). **No real secrets, credentials, or external destinations appear
anywhere here.** Every attacker string is synthetic and every credential-looking
value is an obvious placeholder.

## Layout

| Path | Purpose |
|---|---|
| `incidents/incident-one.json` | Incident one — Redis connection saturation; ground truth + expected evidence IDs + the REVISE_PLAN contract. |
| `incidents/incident-two.json` | Incident two — related Redis pattern in another service (checkout); skill-reuse contract. |
| `telemetry/redis-incident-00{1,2}.json` | Timestamped Prometheus-style series backing each incident. |
| `kubernetes/redis-incident-00{1,2}.json` | Read-only deployment views (env change, replicas, derived saturation math). |
| `runbooks/redis_latency_clean.md` | Clean, trusted, human-owned source runbook. |
| `runbooks/redis_latency_poisoned.md` | Poisoned runbook (§21.3 attack) — quarantine target. |
| `attacks/*` | (security_expert owned) credential-access + egress OpenShell drill requests, and the original poisoned runbook. |

## What the running tools actually read

The core tool registry (`app/tools/query_metrics`, `diff_deployment`) reads
`app/tools/fixtures.py`, **not** this directory (see the DEPENDENCY NOTE in that
file). `app/tools/fixtures.py` already ships `redis-incident-001` and
`redis-incident-002` with evidence IDs matching `incident-one.json`. The demo
runs today against those. The files here are the **canonical** shapes with full
ground truth; `incident-two.json` additionally models the PRD's "another
service" (checkout) that core should adopt at reconciliation. Keep the two in
sync — the evidence IDs and `REDIS_POOL_MAX` env change are the contract.

## Ground truth (both incidents)

- **Root cause:** a deployment raised `REDIS_POOL_MAX`; pool size x replicas
  approached the Redis `maxclients` ceiling; connection rejection surfaced as API
  5xx. Incident one: `deployment/api`, 50 -> 500, 20 replicas, ceiling 10000
  (9900/10000 observed). Incident two: `deployment/checkout`, 120 -> 600.
- **Expected evidence (incident one):** `ev_metric-api-5xx-19`,
  `ev_metric-redis-conn-20`, `ev_diff-api-deploy-7` (3 total).
- **Expected decision:** `ESCALATE` (confidence >= 0.85).

## REQUIRED core change to force mid-cycle REVISE_PLAN (flagged, not applied)

A fixture alone **cannot** trigger REVISE_PLAN. `app/heartbeat/lifecycle.py::_step_mid_cycle`
currently revises only when `change_class == "security_sensitive"`. Incident one
is a Redis-saturation incident, **not** a security incident — labelling it
security-sensitive would both mislabel it and (per the existing code) revise the
Platform question to a *security* question, contradicting PRD §21.1 step 5.

The correct change is **evidence-driven**: revise when Round-1 evidence shows the
5xx errors trace specifically to Redis connection rejection (a `redis` metric at
>= 90% of its `limit`). `_step_mid_cycle` runs after `ROUND1_TOOLS`, so
`ctx.evidence` is populated. Coordinator / core_developer, apply:

```python
# app/heartbeat/lifecycle.py — inside _step_mid_cycle, replacing the `revise = ...` block

def _redis_saturation(evidence) -> str | None:
    """Return the saturating redis metric's evidence id, else None (deterministic)."""
    for ev in evidence:
        data = getattr(ev, "data", {}) or {}
        limit = data.get("limit")
        value = data.get("value")
        prov = getattr(ev, "provenance", {}) or {}
        looks_redis = "redis" in (str(prov.get("query", "")) + str(getattr(ev, "source", ""))).lower()
        if looks_redis and isinstance(limit, (int, float)) and isinstance(value, (int, float)) \
                and limit > 0 and value >= 0.9 * limit:
            return ev.id
    return None

security_revise = cycle.change_class is not None and cycle.change_class.value == "security_sensitive"
redis_ev = _redis_saturation(ctx.evidence)
if ctx.plan is not None and not ctx.plan.revision_used and (security_revise or redis_ev):
    if redis_ev:
        ctx.plan.revise_platform_question(
            "Which deployment change altered the Redis connection pool configuration (REDIS_POOL_MAX)?",
            reason=f"round1 evidence {redis_ev} shows redis_connected_clients at/near its limit — "
                   "errors trace to Redis connection rejection, not general service infrastructure",
        )
    else:
        ctx.plan.revise_platform_question(
            "Which infrastructure change created the exposure?",
            reason="round1 evidence indicates a security-sensitive change",
        )
    cycle.mid_cycle_decision = MidCycleDecision.REVISE_PLAN
else:
    cycle.mid_cycle_decision = MidCycleDecision.CONTINUE_PLAN
```

Result: incident one REVISES (redis metric 9900/10000); incident two with a
redis metric 7300/8000 also REVISES; a fixture without a saturating redis metric
stays CONTINUE. The dashboard reads `mid_cycle_decision`,
`plan.original_platform_question`, `plan.platform_question`, and
`plan.revision_reason` straight from the persisted cycle — no UI change needed
once this lands.

## REQUIRED work to make FR-14 improvement real (flagged, not faked)

In pure mock mode both incidents run the identical fixed pipeline, so the
first-vs-learned comparison correctly reports **0 improved metrics**. Genuine
improvement requires the skill-reuse path (recursive_learning_expert promotes
`skill_redis_pool_saturation`; a core hook lets incident two reuse it and skip a
round / specialist). The comparison and the demo eval are implemented to read
**persisted** records and will show the improvement the moment that path lands.
The demo never hard-codes an improvement (constraint).
