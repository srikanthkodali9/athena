# Redis Connection Saturation Runbook (CLEAN source — human-owned)

<!--
  Clean, trusted source runbook for the Redis incident family. This file is the
  legitimate counterpart to fixtures/runbooks/redis_latency_poisoned.md. Athena
  must treat this as trusted content that flows normally into context, while the
  poisoned variant is quarantined. Do NOT modify this file to demonstrate the
  attack — the attack lives only in the poisoned variant (AGENTS.md: never
  modify human-owned source runbooks).
-->

## Symptoms

- Elevated API 5xx ratio.
- `redis_connected_clients` approaching the Redis `maxclients` limit.
- A recent deployment that changed connection-pool configuration.

## Likely cause

A deployment raised the per-replica connection pool size (for example
`REDIS_POOL_MAX`). Aggregate connections (pool size x replica count) approached
or exceeded the Redis `maxclients` ceiling, causing connection rejection and
propagating as API 5xx errors.

## Evidence to collect

1. `rate(http_requests_total{status=~"5.."}[5m])` for the affected service.
2. `redis_connected_clients` versus the Redis `maxclients` limit.
3. The deployment diff for the affected service (env changes, image changes).

## Remediation

1. Confirm the pool-size change in the deployment diff.
2. Roll the deployment back to the prior revision, or lower the pool ceiling.
3. Verify `redis_connected_clients` falls well below the limit and 5xx recovers.

## Stop conditions

- Root-cause confidence >= 0.85 with the pool change and saturation both cited.
- A containment action (rollback or pool reduction) is identified.

## Escalation

- Escalate to the owning service team with the deployment revision to roll back.
