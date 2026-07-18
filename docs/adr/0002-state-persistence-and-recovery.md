# ADR-0002: State persistence and recovery

- **Status:** Accepted
- **Date:** 2026-07-18
- **Deciders:** Architecture expert, root coordinator
- **Wave:** 1 (foundation)
- **Related:** ADR-0001 (SQLite choice), ADR-0003 (state machine + checkpoints)

## Context

Athena is the only persistent decision-maker. Episodes, skills, proposals,
security history, and **incomplete investigations** must survive restart, and a
forced interruption must resume from a safe checkpoint without repeating costly
or irreversible work (FR-7, PRD §12.3–12.4). The demo must reset and replay
deterministically (NFR Reliability; acceptance criteria "state persists across
restart", "demo resets without manual code changes").

This ADR fixes the SQLite schema-level design, the repository/transaction
boundary, transactional checkpointing, and the **two-level recovery path**.

Traceability: FR-7 (FR7-01…FR7-07), FR-1 (FR1-05, FR1-06), FR-3 (FR3-10 revision
persistence), FR-6, FR-8, FR-9, FR-10, FR-14; TST-03 (restart recovery).

## Decision

### 1. Storage engine and repository boundary

Single SQLite file at `ATHENA_STATE_DB_PATH` (default `.athena/athena.db`,
git-ignored). All access goes through a **thin repository layer** in
`app/memory/` (owned by `core_developer`) exposing typed methods, never raw SQL
to callers. The repository is the only component that opens the connection. This
keeps SQLite swappable (ADR-0001) and is the single home for transaction
control.

SQLite pragmas: `journal_mode=WAL`, `foreign_keys=ON`,
`synchronous=NORMAL`. Single in-process writer (the heartbeat worker); the
overlap lock (FR1-06) plus WAL make concurrent read + single write safe for the
demo.

### 2. Memory boundaries mapped to the schema

The five PRD memory layers (§13.2) are realized as table groups, keeping
**deterministic facts separate from model interpretations, decisions, and
learned procedures** (AGENTS.md rule; FR6-03):

| Memory layer | Tables | Owner writing through repo |
|---|---|---|
| Working (cycle-scoped) | `cycles`, `plans`, `plan_revisions`, `checkpoints` | core |
| Episodic | `episodes`, `incidents` | learning (episodes), core (incidents) |
| Semantic infrastructure | `services`, `dependencies`, `source_trust` | core (facts), security (trust) |
| Pattern / procedural | `skills`, `runbook_proposals` | learning |
| Evidence + audit (fact plane) | `evidence`, `tool_calls`, `model_calls`, `security_decisions`, `comparisons` | core (tool/model/evidence), security (decisions), learning/demo (comparisons) |

### 3. Schema-level design

Column lists are the canonical intent; exact DDL is `core_developer`'s to
author. `*_json` columns hold validated Pydantic payloads. IDs use the prefixed
convention from `docs/api-endpoints.md` (`cycle_`, `inc_`, `ev_`, `skill_`,
`rbp_`, `sec_`).

```text
cycles
  cycle_id PK, trigger, fixture, state, decision,
  phase,                       -- current checkpoint marker (see §5, ADR-0003 states)
  incident_id FK -> incidents,
  state_before_json, state_after_json,
  signals_checked_json, changes_detected_json,
  metrics_json,                -- tokens, latency, ttft, serial-vs-concurrent, calls-avoided
  recovery_json,               -- recovery path taken, if any
  started_at, ended_at, updated_at

plans
  plan_id PK, cycle_id FK, created_at,
  max_specialists, max_tool_calls, max_total_tokens, deadline_seconds,
  round1_json, round2_json,    -- per-round specialist/tool/token/deadline budgets
  midcycle_json,               -- mid-cycle eval budget
  required_evidence_json, stop_conditions_json, security_posture

plan_revisions               -- FR3-10: one row max per cycle in P0
  revision_id PK, cycle_id FK, plan_id FK,
  revision_index,              -- must be 0..1; >1 rejected by app + CHECK
  original_platform_question, original_scope_json,
  decision,                    -- CONTINUE_PLAN | REVISE_PLAN
  trigger_reason,              -- human-readable why
  trigger_evidence_ids_json,   -- Observability evidence IDs that forced revision
  updated_platform_question, created_at

evidence                     -- immutable fact registry (FR6-01); append-only
  evidence_id PK, cycle_id FK, type, source, provenance_json,
  query, window, value_json, summary,
  content_hash, trust_score, created_at

tool_calls
  tool_call_id PK, cycle_id FK, round, tool, args_hash, status,
  evidence_ids_json, latency_ms, created_at

model_calls                  -- one row per inference (FR4-04)
  model_call_id PK, cycle_id FK, phase, role, model_id, tier,
  routing_reason, tokens_in, tokens_out, ttft_ms, latency_ms,
  degraded, repair_retry_used, created_at

specialist_findings          -- model interpretation plane (kept off the fact plane)
  finding_id PK, cycle_id FK, model_call_id FK, role, question,
  facts_json, hypothesis, cited_evidence_ids_json, confidence,
  missing_evidence_json, created_at

security_decisions           -- FR10-04; sanitized, no raw malicious content/secrets
  security_event_id PK, cycle_id FK, stage, producer, consumer,
  finding, severity, confidence, action,          -- allow|redact|exclude|quarantine|block|reject_persistence
  agent_effect, trust_score_before, trust_score_after,
  security_incident_created, created_at

openshell_denials            -- FR11; capability enforcement audit (independent of HiddenLayer)
  denial_id PK, cycle_id FK, action, target, decision, reason, created_at

incidents
  incident_id PK, service, severity, status, assessment_json,
  confidence, recommendation, verified, created_at, updated_at

episodes                     -- compressed verified records (FR7, FR8)
  episode_id PK, incident_id FK, cycle_id FK, compressed_json,
  outcome, verified, known_false_leads_json, created_at

skills                       -- versioned procedural memory (FR8; PRD §13.4)
  skill_id PK, version, status,                    -- observed|candidate|scanned|shadow|validated|promoted|deprecated|quarantined
  domain, triggers_json, preconditions_json, procedure,
  risk, requires_approval, provenance_json,        -- created_from_incidents
  hiddenlayer_status, validation_passes, confidence,
  known_false_leads_json, created_at, updated_at

runbook_proposals            -- FR9; never mutates source
  proposal_id PK, source_ref, diff, provenance_json,
  validation_status, hiddenlayer_status, risk, required_reviewer, created_at

comparisons                  -- FR14 first-vs-learned
  comparison_id PK, first_incident_id FK, learned_incident_id FK,
  deltas_json, created_at

checkpoints                  -- completed-step ledger for idempotent recovery (§5)
  checkpoint_id PK, cycle_id FK, phase, task_key, output_ref_json, created_at
  UNIQUE(cycle_id, phase, task_key)

source_trust                 -- semantic trust facts, updated by security
  source_id PK, trust_score, last_security_event_id, updated_at

services, dependencies       -- stable semantic infrastructure facts (fixtures-seeded)
```

Separation guarantees: `evidence`, `tool_calls`, `model_calls` are the **fact /
provenance plane** (append-only, never overwritten). `specialist_findings`,
`incidents.assessment`, `skills` are the **interpretation / procedural plane**.
Decisions live in `cycles.decision`, `plan_revisions`, and `security_decisions`.
A finding cites evidence by ID; it never mutates it.

### 4. Transactional checkpointing

Every phase transition in the state machine (ADR-0003) is committed in **one
transaction** that atomically: (a) inserts the phase's durable outputs (plan,
evidence, findings, model_calls, mid-cycle decision, etc.), (b) writes a
`checkpoints` row `(cycle_id, phase, task_key)`, and (c) advances `cycles.phase`
and `cycles.updated_at`. If the process dies mid-phase, the transaction rolls
back and the phase is retried whole — no partial state is observable.

**Idempotency keys (FR1-06, FR7-07):** each expensive unit of work is keyed by
`(cycle_id, phase, task_key)`. Before running a tool call or model call, the
worker checks `checkpoints`/`model_calls`/`tool_calls` for an existing result
and reuses it instead of re-invoking. Manual heartbeats and lifecycle commands
carry an `Idempotency-Key` (api-endpoints.md) so a retried request never
double-starts a cycle.

### 5. Two-level recovery path

Both levels share the same persistence, checkpoints, and HiddenLayer-inspection
requirements (PRD §13.1a). Recovery always **revalidates stale evidence** before
resuming (FR7-06): evidence older than a per-source TTL is re-collected; fresh
persisted model/tool outputs are reused.

**Level 1 — within-cycle crash recovery (unplanned interruption).**
On startup / next heartbeat the worker loads any cycle whose `state` is
non-terminal, reads `cycles.phase` and the `checkpoints` ledger, and **resumes
from the next uncompleted phase**. Completed, persisted outputs (plan, Round 1
evidence + Observability finding, mid-cycle decision, Round 2 finding) are not
recomputed — this is what prevents repeating costly model/tool calls
(FR7-07). Costly or irreversible actions are gated behind checkpoints so they
are executed at most once. The recovery path taken is written to
`cycles.recovery_json` (FR7 acceptance; TST-03).

**Level 2 — cross-cycle CONTINUE (deliberate).**
When a cycle cannot reach sufficient confidence in one heartbeat (longer polling
interval, pending human review, evolving situation), Athena emits a `CONTINUE`
decision that persists the partial investigation as a resumable checkpoint and
closes the cycle cleanly. The next heartbeat loads that checkpoint and resumes,
feeding prior findings into the next hypothesis (recursive refinement across
intervals). This is a normal terminal state for the current cycle, not an error
path.

| Dimension | Level 1 (crash) | Level 2 (CONTINUE) |
|---|---|---|
| Trigger | Process death / restart | Explicit `CONTINUE` decision |
| Cycle state at resume | non-terminal (interrupted) | terminal (`continued`) |
| Resume unit | Next phase in same logical investigation | New cycle picks up prior findings |
| Repeats costly work? | No — reuses checkpointed outputs | No — carries forward persisted findings |
| Recorded in | `cycles.recovery_json` | `episodes` / next `cycles.state_before_json` |

### 6. Reset and replay

`scripts/reset_demo.sh` deletes `.athena/athena.db` and re-seeds semantic
fixtures, returning the workspace to a known state with no code changes
(acceptance criterion). Because evidence, tool, and model outputs are persisted
by `(cycle_id, phase, task_key)`, a mock-mode replay reproduces a cycle
deterministically (ADR-0001 §2).

## Consequences

- **Positive:** restart-safe, idempotent, deterministically resettable; clean
  fact/interpretation/decision separation; both recursive loops share one
  mechanism.
- **Negative / trade-offs:** SQLite single-writer and `*_json` columns trade
  query richness for simplicity — acceptable for a single-incident-family demo;
  the repository port allows a later swap.
- **Follow-ups:** `core_developer` owns DDL + migrations and the repository API;
  `recursive_learning_expert` owns `episodes`/`skills`/`runbook_proposals`
  writes through that repository; `security_expert` owns `security_decisions`,
  `openshell_denials`, and `source_trust` writes.

## Alternatives considered

- **JSON files on disk:** rejected — no transactional checkpointing, unsafe
  partial writes, hard to guarantee idempotent recovery.
- **Event-sourced log only:** rejected for P0 — more machinery than the demo
  needs; the checkpoints ledger gives the needed replay/idempotency without it.
- **Separate DB per plane:** rejected — cross-plane transactions (commit cycle +
  episode + security decision atomically) are simpler in one file.
