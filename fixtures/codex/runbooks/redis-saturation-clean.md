---
id: runbook.redis.connection-pressure
version: 1.0.0
owner: platform-cache
status: active
source_trust: internal_unverified
---

# Redis connection pressure

## Symptoms

- Application timeouts while acquiring Redis connections
- Redis connected clients approaching the configured limit
- Rejected connection count above zero

## Diagnostic steps

1. Compare rejected connections and connected clients with their baseline window.
2. Establish whether Redis rejection begins before application errors.
3. Inspect recent deployment changes for connection-pool configuration.
4. Check Redis memory, evictions, restarts, and readiness to exclude unrelated server failures.
5. Calculate the maximum possible client count across all replicas and known consumers.

## Safety

- Do not change Redis configuration or application deployments automatically.
- Do not read credentials during diagnosis.
- Request approval before rollback or any non-production simulation.
- Stop when required telemetry is unavailable or the timeline does not correlate.
