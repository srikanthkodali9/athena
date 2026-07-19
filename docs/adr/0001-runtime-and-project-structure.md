# ADR-0001: Runtime and project structure

- **Status:** Accepted
- **Date:** 2026-07-18
- **Deciders:** Architecture expert, root coordinator
- **Wave:** 1 (foundation)
- **Supersedes:** none
- **Related:** ADR-0002 (state persistence), ADR-0003 (two-round investigation contract)

## Context

Athena must be built, reset, and demonstrated reproducibly on a MacBook while
serving real inference from remote NVIDIA GPU endpoints, real HiddenLayer
Runtime Security, and real OpenShell enforcement. Three P0 agents build the
application concurrently in a shared workspace (`core_developer`,
`security_expert`, and — in Wave 2 — `inference_engineer`), so the runtime stack
and the `app/` package layout must be fixed before code lands to prevent
divergent structure and ownership collisions.

The stack below was **locked by the root coordinator** as the standard,
demo-critical baseline. This ADR ratifies those choices and freezes the package
layout so it matches `.agents/manifest.yaml` ownership exactly. These are
conventional choices for a Python service; no deviation is warranted for the
demo.

Traceability: FND-01, FND-02, FND-05; FR-1, FR-4, FR-7, FR-10, FR-11, FR-12;
NFR Portability; Acceptance criteria "state persists across restart", "inference
runs through vLLM", "demo resets without manual code changes".

## Decision

### 1. Runtime stack (locked — accepted)

| Concern | Choice | Rationale (demo-critical) |
|---|---|---|
| Language | Python **3.12** | Matches local toolchain (`python 3.12.13` verified) and typed-Python engineering rule. |
| Env / deps | **uv** with PEP 621 `pyproject.toml` | Fast, reproducible resolution; single lockfile; verified `uv 0.11.2` present. |
| API | **FastAPI + uvicorn** | ASGI, native Pydantic v2, thin route handlers (AGENTS.md rule). Preserves the `/api/v1` surface. |
| Event stream | **Server-Sent Events** via `sse-starlette` | One-way cycle/Council progress to the dashboard; matches `text/event-stream` in `docs/api-endpoints.md`. No broker needed. |
| Schemas + config | **Pydantic v2** + **pydantic-settings** | Explicit typed schemas (engineering rule); `.env.example` is the canonical config surface. |
| Durable state | **SQLite** single file at `.athena/athena.db` behind a repository layer | Local, transactional, restart-safe, resettable. Swappable via the repository port. See ADR-0002. |
| HTTP clients | **httpx** | Async, OpenAI-compatible calls to vLLM and the HiddenLayer adapter; per-call timeouts. |
| Tests | **pytest + pytest-asyncio** | Async orchestration and adapter tests; test groups per AGENTS.md. |

### 2. External services: live-first with deterministic mocks (required)

Real external services are available and are the default live target: live vLLM
(Nemotron-Super strategic + Nemotron-Nano council), real HiddenLayer, real
OpenShell. Every external dependency is reached through a **typed adapter behind
a Protocol port**, selected by config (`ATHENA_INFERENCE_MODE`,
`ATHENA_SECURITY_MODE`, `ATHENA_KUBERNETES_MODE`). Each port **must** also ship a
deterministic mock adapter — mocks are mandatory for reset/replay determinism
and for the Wave-1 core spine, not a fallback afterthought. This is **not** a
mock-only system: `live` is the demo path; `mock` guarantees reproducibility.

### 3. Package layout (frozen to manifest ownership)

```text
athena/
├── pyproject.toml                     # core_developer
├── app/
│   ├── __init__.py
│   ├── api/                           # core_developer — FastAPI routers (thin), SSE
│   ├── heartbeat/                     # core_developer — scheduler, manual trigger, overlap lock
│   ├── athena/                        # core_developer — orchestrator, state machine,
│   │   │                             #   planner glue, synthesis glue, shared schemas + ports
│   │   ├── schemas.py                 #   canonical cross-cutting Pydantic domain schemas
│   │   └── ports.py                   #   InferencePort + repository/tool Protocols core defines
│   ├── memory/                        # core_developer — repository layer over SQLite (ADR-0002)
│   ├── tools/                         # core_developer — deterministic tool registry + evidence
│   ├── models/                        # inference_engineer — provider port + mock/vLLM adapters
│   ├── routing/                       # inference_engineer — tiered model/tool router
│   ├── council/                       # inference_engineer — Observability + Platform specialists
│   ├── security/                      # security_expert — SecurityGateway, contracts.py, adapters
│   ├── policies/                      # security_expert — OpenShell policy engine
│   ├── learning/                      # recursive_learning_expert — episode→skill pipeline
│   └── ui/                            # demo_engineer — dashboard assets
├── prompts/{athena,council}/          # inference_engineer
├── skills/{candidates,validated,quarantined,deprecated}/   # recursive_learning_expert
├── runbooks/{source,proposals}/       # source read-only; proposals = recursive_learning_expert
├── policies/openshell.yaml            # security_expert
├── fixtures/{incidents,telemetry,kubernetes,runbooks,attacks}/  # demo_engineer / security_expert (attacks)
├── tests/{core,security,inference,learning,demo}/
├── scripts/{start_demo.sh,reset_demo.sh,run_replay.sh}/    # demo_engineer
└── docs/                              # architecture_expert (adr/, architecture.md, api-endpoints.md)
```

This is the PRD §25 structure with two P0 simplifications recorded here:

- The PRD §25 `app/council/` folder lists `security.py`, `historian.py`, and
  `critic.py`. For P0 those are **not created** — Observability and Platform are
  the only specialist-model workers (AGENTS.md, FR-5). Historian retrieval is a
  deterministic tool, Security interpretation and the Critic role are
  Nemotron-Super prompts. See ADR-0003 and the "P0-blocking inconsistencies
  resolved" section of `docs/architecture.md`.
- Shared cross-cutting schemas live in a **core-owned** module
  (`app/athena/schemas.py`) so every plane imports one canonical definition set;
  security contracts remain owned by `security_expert` in
  `app/security/contracts.py`, and skill/episode schemas remain owned by
  `recursive_learning_expert` in `app/learning/`. Core references learning and
  security types by ID or by importing the owner's module — never by
  re-declaring them — to avoid circular ownership. See the contract inventory in
  `docs/architecture.md`.

### 4. Ports core defines vs. ports other agents own

- **InferencePort** (OpenAI-compatible chat/completions Protocol) is **defined
  by core** in `app/athena/ports.py` and **consumed** by the orchestrator. Core
  ships a deterministic mock in Wave 1; `inference_engineer` implements the live
  vLLM adapter in `app/models/` in Wave 2. Core depends on the Protocol, never
  on a vendor SDK.
- **SecurityGateway** Protocol and its request/decision contracts are **owned by
  `security_expert`** in `app/security/contracts.py`. Core depends on that
  Protocol. Recorded here for convergence; not owned by this ADR.

### 5. Configuration surface

`.env.example` is canonical. Config is loaded once through a pydantic-settings
`Settings` object (`app/athena` or a core config module) with safe defaults
(`ATHENA_INFERENCE_MODE=live`, `ATHENA_SECURITY_MODE=live`,
`ATHENA_SECURITY_FAIL_CLOSED=true`, `ATHENA_STATE_DB_PATH=.athena/athena.db`).
Secrets are never committed; `.env`, `*.db`, and `.athena/` are git-ignored
(verified). Health/readiness routes must not leak credentials or full provider
URLs (`docs/api-endpoints.md`).

## Consequences

- **Positive:** stable, non-overlapping ownership; one runtime dialect;
  reproducible env; live-first with deterministic reset; no broker/microservice
  sprawl (AGENTS.md constraint).
- **Negative / trade-offs:** SQLite is single-writer — mitigated by the
  in-process heartbeat scheduler and cycle-level locking (ADR-0002). The
  repository port keeps a future swap cheap.
- **Follow-ups:** `core_developer` authors `pyproject.toml` and
  `app/athena/{schemas,ports}.py`; `inference_engineer` and `security_expert`
  confirm their Protocol shapes against the contract inventory.

## Alternatives considered

- **Postgres / external DB:** rejected for P0 — needs a running service, breaks
  one-command local reset, violates "smallest architecture that proves the
  thesis". Repository port leaves the door open.
- **WebSockets instead of SSE:** rejected — cycle progress is one-directional;
  SSE is simpler and already specified in the API doc.
- **Poetry / pip-tools:** rejected in favor of the locked `uv` choice; faster and
  already installed.
- **Mock-only system:** explicitly rejected by coordinator direction — real
  services are the demo target.
