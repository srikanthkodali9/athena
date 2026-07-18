# Functional-requirement backlog

This backlog decomposes the proposal's FR-1 through FR-14 into implementable tasks. Task IDs are stable references for issues, commits, tests, and status reporting.

## Status convention

- `[ ]` Not started
- `[~]` In progress
- `[x]` Complete and verified

## Foundation tasks

- [ ] **FND-01 — Application skeleton:** create the typed Python package structure for API, heartbeat, Athena, Council, routing, tools, memory, learning, security, policies, models, and UI.
- [ ] **FND-02 — Configuration:** provide validated configuration for providers, endpoints, heartbeat cadence, budgets, storage, security mode, and degraded-mode behavior.
- [ ] **FND-03 — Shared schemas:** define versioned schemas for cycles, plans, evidence, findings, model calls, tool calls, security decisions, episodes, skills, and runbook proposals.
- [ ] **FND-04 — Demo fixtures:** create Redis incident one, related incident two, baseline telemetry, deployment diffs, runbooks, and poisoned-content fixtures.
- [ ] **FND-05 — Test harness:** configure unit, integration, adversarial, replay, and performance test groups.
- [ ] **FND-06 — Operational telemetry:** establish structured logging, correlation IDs, latency measurements, token usage, and error reporting.

---

## FR-1 — Heartbeat operation

**Requirement:** Athena runs periodic and manual heartbeat cycles.

- [ ] **FR1-01:** implement a configurable periodic scheduler.
- [ ] **FR1-02:** expose a manual heartbeat trigger for development and demo use.
- [ ] **FR1-03:** define the cycle state machine and allowed decision values.
- [ ] **FR1-04:** load pending tasks, incomplete investigations, signals, and security events at cycle start.
- [ ] **FR1-05:** persist cycle start, decision, end state, metrics, errors, and recovery actions.
- [ ] **FR1-06:** prevent overlapping cycles or explicitly coordinate them with locks and idempotency keys.

**Acceptance:** Athena detects the first incident without a user prompt, and a manual trigger produces the same workflow.

## FR-2 — Change detection

**Requirement:** Athena avoids full investigation when monitored inputs are unchanged.

- [ ] **FR2-01:** create checkpoints for monitored metrics, events, repository files, runbooks, and policy events.
- [ ] **FR2-02:** calculate hashes, versions, or window summaries appropriate to each source.
- [ ] **FR2-03:** classify changes as irrelevant, meaningful, security-sensitive, or requiring revalidation.
- [ ] **FR2-04:** implement a no-change `WAIT` path that does not call specialists.
- [ ] **FR2-05:** record what was checked and why investigation was skipped or started.

**Acceptance:** an unchanged heartbeat finishes in under five seconds and launches no full investigation.

## FR-3 — Investigation planning

**Requirement:** Athena produces a bounded plan containing specialists, tools, budgets, evidence, and stop conditions.

- [ ] **FR3-01:** define and validate the investigation-plan schema.
- [ ] **FR3-02:** include maximum specialists, tool calls, tokens, and deadline.
- [ ] **FR3-03:** identify required evidence and allowed sources for each task.
- [ ] **FR3-04:** define confidence, containment, no-new-evidence, and deadline stop conditions.
- [ ] **FR3-05:** attach the required security posture and approval requirements.
- [ ] **FR3-06:** reject plans that exceed policy or omit mandatory bounds.

**Acceptance:** no specialist, model, or investigation tool executes until a valid bounded plan exists.

## FR-4 — Tiered model routing

**Requirement:** bounded tasks use smaller models; strategic decisions use the main Nemotron model.

- [ ] **FR4-01:** implement the deterministic, specialist, Athena, and critic routing tiers.
- [ ] **FR4-02:** encode routing by task complexity, risk, required capability, and evidence type.
- [ ] **FR4-03:** preserve OpenAI-compatible provider interfaces.
- [ ] **FR4-04:** record model ID, role, routing reason, tokens, latency, and degraded mode for every inference.
- [ ] **FR4-05:** add bounded fallback behavior for unavailable models.
- [ ] **FR4-06:** prevent high-risk recommendations in degraded mode without approval.

**Acceptance:** the demo visibly routes narrow interpretation to a small model and strategic work to Nemotron.

## FR-5 — Specialist coordination

**Requirement:** at least three Council domains return structured, evidence-backed output.

- [ ] **FR5-01:** implement the Observability specialist contract.
- [ ] **FR5-02:** implement the Platform specialist contract.
- [ ] **FR5-03:** implement the Historian domain using filtered retrieval, reranking, and bounded comparison.
- [ ] **FR5-04:** add Security specialist behavior for the poisoned-runbook scenario.
- [ ] **FR5-05:** implement Critic behavior with no execution capability.
- [ ] **FR5-06:** restrict every prompt to one question, allowed evidence IDs, allowed tools, prohibitions, and a confidence rubric.
- [ ] **FR5-07:** execute independent Council tasks concurrently.
- [ ] **FR5-08:** schema-validate all outputs and allow at most one structured-output repair retry.

**Acceptance:** three domains return valid findings containing evidence IDs, confidence, and missing evidence; no specialist makes the final decision.

## FR-6 — Evidence-backed findings

**Requirement:** every operational claim references verifiable evidence.

- [ ] **FR6-01:** implement an immutable evidence registry with unique IDs and provenance.
- [ ] **FR6-02:** standardize deterministic tool-result envelopes.
- [ ] **FR6-03:** separate facts, interpretations, hypotheses, and recommendations in all schemas.
- [ ] **FR6-04:** validate that every finding cites permitted evidence IDs.
- [ ] **FR6-05:** reject or lower confidence for unsupported and contradictory claims.
- [ ] **FR6-06:** expose evidence details and source trust in the dashboard.

**Acceptance:** every displayed root-cause statement and recommendation can be traced to metrics, files, objects, incidents, skills, graph paths, or security events.

## FR-7 — Persistent memory

**Requirement:** episodes, skills, proposals, security history, and incomplete work survive restart.

- [ ] **FR7-01:** select and document the durable storage implementation.
- [ ] **FR7-02:** persist heartbeat cycles, tasks, evidence references, episodes, security decisions, and performance metrics.
- [ ] **FR7-03:** implement working, episodic, semantic, pattern, and procedural memory boundaries.
- [ ] **FR7-04:** checkpoint incomplete investigations transactionally.
- [ ] **FR7-05:** reload completed and pending actions after restart.
- [ ] **FR7-06:** revalidate stale evidence before resuming work.
- [ ] **FR7-07:** prevent repeated expensive or irreversible actions during recovery.

**Acceptance:** a forced restart during an investigation resumes from a safe checkpoint without corrupting or duplicating state.

## FR-8 — Recursive skill writing

**Requirement:** Athena creates versioned candidate skills from verified patterns.

- [ ] **FR8-01:** define verification status for incident outcomes.
- [ ] **FR8-02:** extract reusable lessons only from verified evidence and outcomes.
- [ ] **FR8-03:** implement the versioned skill schema, provenance, triggers, preconditions, procedure, risk, and stop conditions.
- [ ] **FR8-04:** implement the observed, candidate, scanned, shadow, validated, promoted, deprecated, and quarantined lifecycle.
- [ ] **FR8-05:** scan candidate content through HiddenLayer before persistence.
- [ ] **FR8-06:** build deterministic replay validation and record validation history.
- [ ] **FR8-07:** require additional evidence or approval for weak or high-risk candidates.
- [ ] **FR8-08:** exclude quarantined artifacts from retrieval.

**Acceptance:** incident one creates a scanned candidate skill that is promoted only after replay validation succeeds.

## FR-9 — Runbook improvement

**Requirement:** Athena produces reviewable diffs without overwriting source runbooks.

- [ ] **FR9-01:** load source runbooks as read-only, untrusted content.
- [ ] **FR9-02:** generate patches in a separate proposal directory.
- [ ] **FR9-03:** include evidence, provenance, validation status, HiddenLayer result, risk, and required reviewer.
- [ ] **FR9-04:** support new steps, rollback guidance, stale-warning annotations, ownership corrections, and new-runbook proposals.
- [ ] **FR9-05:** validate that proposal application would not modify protected content silently.
- [ ] **FR9-06:** add an approval request for submission or draft PR creation.

**Acceptance:** Athena produces a reviewable diff while the human-owned source remains byte-for-byte unchanged.

## FR-10 — Runtime security

**Requirement:** HiddenLayer inspects model, tool, retrieval, and persistence boundaries.

- [ ] **FR10-01:** implement one security-gateway interface for all inspection stages.
- [ ] **FR10-02:** cover heartbeat input, retrieved content, model prompts, specialist responses, Athena responses, tool calls, tool results, and persistent writes.
- [ ] **FR10-03:** implement log, redact, exclude, quarantine, block, alert, and persistence-rejection actions.
- [ ] **FR10-04:** record security findings with producer, consumer, stage, severity, confidence, action, and trust-score change.
- [ ] **FR10-05:** reduce source trust and link findings to a security incident.
- [ ] **FR10-06:** fail closed for actionable tool calls and writes when inspection is unavailable.
- [ ] **FR10-07:** permit only explicitly restricted behavior for non-actionable summaries during an outage.

**Acceptance:** poisoned instructions are excluded from prompts and memory, a security event is created, and trusted investigation work continues.

## FR-11 — Sandboxed capability

**Requirement:** OpenShell permits useful work while denying protected actions.

- [ ] **FR11-01:** create a versioned OpenShell policy with allow, deny, and approval-gated categories.
- [ ] **FR11-02:** allow approved repository, telemetry, runbook, model, security, diagnostic, and agent-owned persistence operations.
- [ ] **FR11-03:** deny protected credential paths and arbitrary home-directory reads.
- [ ] **FR11-04:** deny unapproved network egress and collected-data export.
- [ ] **FR11-05:** deny production mutation, destructive commands, protected-branch pushes, and security-policy changes.
- [ ] **FR11-06:** grant each Council role only its documented data and execution capabilities.
- [ ] **FR11-07:** add positive and negative policy tests.

**Acceptance:** approved diagnostics work, while protected credential access and an unapproved endpoint are independently denied and audited.

## FR-12 — Model serving

**Requirement:** real demo inference passes through vLLM.

- [ ] **FR12-01:** validate a supported Nemotron model against the actual GPU and vLLM version.
- [ ] **FR12-02:** deploy the strategic OpenAI-compatible endpoint.
- [ ] **FR12-03:** deploy or configure the specialist endpoint.
- [ ] **FR12-04:** add health checks, timeouts, bounded retries, and explicit degraded mode.
- [ ] **FR12-05:** run independent Council calls concurrently.
- [ ] **FR12-06:** record requests, time to first token, total latency, tokens per second, token counts, GPU use, and model role.
- [ ] **FR12-07:** benchmark serial versus concurrent execution and tiered routing versus main-model-only routing.
- [ ] **FR12-08:** save reproducible launch configuration and rehearsal steps.

**Acceptance:** the demo proves real vLLM inference and displays concurrency and model-tier metrics.

## FR-13 — Nemotron centrality

**Requirement:** Nemotron performs planning, synthesis, contradiction resolution, and learning.

- [ ] **FR13-01:** implement the Nemotron heartbeat-interpretation prompt and schema.
- [ ] **FR13-02:** use Nemotron for investigation planning, specialist selection, and routing decisions.
- [ ] **FR13-03:** use Nemotron for cross-domain evidence synthesis and final operational judgment.
- [ ] **FR13-04:** implement a separate adversarial critic prompt for high-impact recommendations.
- [ ] **FR13-05:** use Nemotron for lesson extraction, candidate skill drafting, and runbook proposal drafting.
- [ ] **FR13-06:** add evaluation fixtures for planning, structured output, evidence citation, contradictions, and learning quality.

**Acceptance:** disabling Nemotron removes Athena's planning, strategic synthesis, and learning capabilities rather than merely changing text generation.

## FR-14 — Measurable improvement

**Requirement:** a related incident reuses validated knowledge and improves at least two metrics.

- [ ] **FR14-01:** define target thresholds for investigation time, specialist calls, tool calls, main-model calls, confidence, unsupported hypotheses, and recommendation specificity.
- [ ] **FR14-02:** capture a complete incident-one baseline.
- [ ] **FR14-03:** retrieve the validated Redis skill during incident two.
- [ ] **FR14-04:** compare matching and differing signals before applying the skill.
- [ ] **FR14-05:** run incident two under the same measurement conditions.
- [ ] **FR14-06:** calculate and persist metric deltas.
- [ ] **FR14-07:** display first-versus-learned results side by side.
- [ ] **FR14-08:** fail the demo evaluation when fewer than two metrics improve.

**Acceptance:** incident two reuses a validated skill and visibly improves at least two agreed metrics.

---

## Cross-cutting verification

- [ ] **TST-01:** unit tests cover parsers, schemas, routing, evidence validation, security policy, persistence, and replay.
- [ ] **TST-02:** an integration test covers one complete heartbeat from change detection through final persistence.
- [ ] **TST-03:** a restart test covers recovery from an incomplete cycle.
- [ ] **TST-04:** adversarial tests cover prompt injection, poisoned memory, credential access, and exfiltration.
- [ ] **TST-05:** performance tests verify no-change, small-investigation, full-investigation, runbook-proposal, and dashboard-update targets.
- [ ] **TST-06:** deterministic reset and replay work without manual code changes.
- [ ] **TST-07:** every acceptance criterion is mapped to an automated test or an explicit demo verification step.
