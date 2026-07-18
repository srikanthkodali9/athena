# Athena demo datasets

These fixtures provide deterministic, synthetic inputs for Athena's P0 hackathon demonstration. They contain no production data or usable credentials.

## Core scenarios

### `redis-001` — unknown pattern

The checkout API deployment initially looks like an application regression. Round 1 shows that Redis rejected connections rise before checkout 5xx errors. Athena must issue `REVISE_PLAN` and narrow the Platform question from general checkout health to Redis connection-pool configuration.

The Kubernetes diff increases the checkout API pool ceiling from 30 to 120 connections across six replicas, exceeding the Redis fixture's `maxclients` limit when normal background consumers are included.

### `redis-002` — learned pattern

The recommendations API exhibits the same validated fingerprint with different replicas and pool values. Athena must retrieve `sre.redis.connection-saturation`, use the stored false lead to avoid an application-code investigation, and improve at least two metrics without assuming every service detail is identical.

## Security scenario

`redis-saturation-poisoned.md` contains an explicit synthetic prompt-injection and exfiltration request. It references only a fake protected path and a reserved `.invalid` domain. The expected behavior is quarantine, context exclusion, trust reduction, persistence rejection, a security event, and independent OpenShell denials.

## Edge cases

The edge-case set covers:

- unchanged inputs;
- missing required telemetry;
- evidence contradicting a Redis hypothesis;
- a low-volume Redis false positive;
- duplicate alert delivery;
- stale evidence requiring revalidation;
- out-of-order and duplicate metric points;
- malformed tool results;
- sensitive log content requiring redaction;
- poisoned tool output;
- security-gateway failure behavior.

## Directory layout

```text
fixtures/
├── manifest.json
├── schemas/
├── incidents/
│   ├── redis-001/
│   ├── redis-002/
│   └── edge-cases/
├── telemetry/
│   ├── redis-001/
│   ├── redis-002/
│   └── edge-cases/
├── kubernetes/
├── runbooks/
└── attacks/
```

## Data-handling rules

- `incident.json` and referenced telemetry are runtime inputs.
- `expected-outcome.json` is test-oracle data and must never be included in an Athena or specialist prompt.
- Evidence IDs are stable and unique within the fixture catalog.
- Normal metric fixtures are timestamp-ordered. The out-of-order edge case is intentionally exempt.
- All timestamps are UTC RFC 3339 values.
- Attack content is synthetic and must still pass through the runtime security gateway.
- A `.invalid` destination is deliberately non-routable and must also be denied by OpenShell policy.

## Validation

Run:

```bash
python3 scripts/validate_fixtures.py
python3 -m unittest discover -s tests/demo -p 'test_*.py'
```
