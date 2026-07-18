# Core developer

## Mission

Build Athena's smallest reliable application spine: configuration, API, heartbeat lifecycle, bounded orchestration, evidence tools, durable state, and restart recovery.

## Initial assignment

Implement FND-01 and the minimum shared schemas needed for:

- `GET /api/v1/health`
- `GET /api/v1/ready`
- `POST /api/v1/heartbeats`
- persisted cycle creation and retrieval
- deterministic fixture execution through typed tool ports

Use mock inference and security ports until their owning agents deliver adapters.

## Owned paths

- `pyproject.toml`
- `app/api/`
- `app/heartbeat/`
- `app/athena/`
- `app/memory/`
- `app/tools/`
- `tests/core/`

Changes elsewhere require coordinator approval.

## Required deliverables

- Runnable local application entry point
- Typed settings with safe defaults and `.env.example` support coordinated through the root
- Cycle, plan, evidence, tool-result, decision, and checkpoint schemas required by core
- Periodic/manual heartbeat command path with overlap protection
- Durable cycle storage and safe restart behavior
- Deterministic tool registry with at least fixture-backed metrics and deployment-diff tools
- Focused tests for state transitions, idempotency, evidence registration, API behavior, and recovery

## Implementation constraints

- Keep route handlers thin.
- Depend on inference and security through explicit ports, not vendor SDKs.
- Store evidence IDs rather than embedding raw evidence into every object.
- Do not let a model response become a fact without schema and evidence validation.
- Bound plans by tool calls, model calls, tokens, deadline, and one revision.
- Make the no-change path avoid model calls.
- Preserve source runbooks as read-only.

## Verification

- Run core unit tests.
- Run one integration test from manual heartbeat creation through persisted completion using deterministic mocks.
- Verify a restart can load an incomplete cycle without repeating completed work.

## Do not

- Implement live vLLM, HiddenLayer, or Kubernetes clients.
- Create UI components.
- Implement P2 specialists.
- Add a background platform before an in-process scheduler proves insufficient.

## Handoff

Use the repository handoff format and identify the exact inference, security, learning, and UI ports that are ready for integration.
