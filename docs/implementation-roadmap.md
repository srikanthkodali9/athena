# Implementation roadmap

The roadmap prioritizes a complete, secure demonstration over broad integration. P2 work must not begin until the P0 story is stable and repeatable.

## Phase 0 — Lock the demo contract

**Outcome:** the team agrees on exactly what will be demonstrated and measured.

- Select the supported main Nemotron and specialist model targets.
- Confirm GPU and vLLM compatibility early.
- Define the Redis incident-one, incident-two, and poisoned-runbook fixtures.
- Choose the three Council domains required for FR-5.
- Define how fixture outcomes become verified.
- Set at least two numeric improvement thresholds for FR-14.
- Choose heartbeat cadence and the durable storage implementation.
- Decide which HiddenLayer inspection stages are required for P0.

**Exit gate:** fixtures, models, success thresholds, and unresolved requirement decisions are documented.

## Phase 1 — Build the typed foundation

**Outcome:** components share stable contracts and can be tested independently.

- Create the Python package and configuration structure.
- Implement the public and internal contracts defined in the [API and endpoint map](api-endpoints.md).
- Implement cycle, plan, evidence, finding, model-call, tool-call, security, episode, skill, and proposal schemas.
- Add structured logging and correlation IDs.
- Create deterministic start, reset, and replay entry points.
- Establish unit, integration, adversarial, and performance test groups.

**Primary requirements:** FND-01 through FND-06.

**Exit gate:** schemas validate representative fixtures and reset returns the workspace to a known state.

The target component and deployment boundaries are maintained in the [architecture diagrams](architecture.md).

## Phase 2 — Secure evidence plane

**Outcome:** Athena can safely collect facts before model orchestration is introduced.

- Implement metrics, logs, deployment-diff, repository-read, dependency, blast-radius, security-scan, and memory-search tool contracts.
- Add evidence IDs, provenance, schema validation, and source trust.
- Implement monitored-input checkpoints and the no-change path.
- Route retrieval, tool calls, and tool results through the HiddenLayer gateway.
- Create and test the OpenShell allow, deny, and approval-gated policy.

**Primary requirements:** FR-2, FR-6, FR-10, and FR-11.

**Exit gate:** deterministic tools reproduce the Redis evidence, unchanged inputs skip investigation, poisoned evidence is quarantined, and policy denials are proven.

## Phase 3 — Inference and Council

**Outcome:** real, bounded, observable model work runs through vLLM.

- Deploy and validate Nemotron and specialist endpoints.
- Implement the model-provider abstraction and tiered router.
- Add Observability, Platform, and Historian structured contracts.
- Add Security and Critic contracts required by the demo flow.
- Enforce narrow prompts, allowed evidence, token limits, and confidence rubrics.
- Execute independent Council calls concurrently.
- Capture role, model, tokens, time to first token, total latency, throughput, and GPU metrics.

**Primary requirements:** FR-4, FR-5, FR-12, and FR-13.

**Exit gate:** three Council domains return evidence-backed JSON through real vLLM inference, and Nemotron produces a valid bounded plan and synthesis.

## Phase 4 — Heartbeat, orchestration, and recovery

**Outcome:** Athena operates autonomously and survives interruption.

- Implement periodic and manual heartbeat triggers.
- Load state, changes, security events, and pending work.
- Produce a bounded investigation plan.
- Enforce specialist, tool, token, and deadline budgets.
- Coordinate deterministic tools and concurrent specialists.
- Resolve contradictions, run the critic, assign confidence, and choose a final decision.
- Persist checkpoints and resume incomplete cycles after restart.

**Primary requirements:** FR-1, FR-3, FR-6, FR-7, and FR-13.

**Exit gate:** incident one runs end to end without a prompt and resumes safely after a forced interruption.

## Phase 5 — Recursive learning and runbooks

**Outcome:** a verified incident becomes protected, reusable procedural knowledge.

- Compress and persist the verified episode.
- Extract a Redis pattern and draft a versioned candidate skill.
- Scan the skill before persistence.
- Replay the skill against positive, negative, and contradictory fixtures.
- Promote, retain, reject, or quarantine based on validation.
- Draft a separate runbook patch with provenance and reviewer requirements.
- Prevent quarantined artifacts from entering retrieval.

**Primary requirements:** FR-7, FR-8, FR-9, and FR-10.

**Exit gate:** the Redis skill is promoted only after security inspection and replay validation, and the original runbook remains unchanged.

## Phase 6 — Improvement proof and dashboard

**Outcome:** judges can see autonomy, evidence, learning, and security behavior clearly.

- Run incident one and store baseline measurements.
- Run incident two with validated-skill retrieval.
- Display evidence, plan, Council activity, routing, confidence, and critic output.
- Compare time, model calls, specialist calls, tool calls, confidence, and unsupported hypotheses.
- Display HiddenLayer quarantine, trust change, persistence rejection, and security incidents.
- Display OpenShell protected-path and egress denials.

**Primary requirements:** FR-6, FR-10, FR-11, FR-12, and FR-14.

**Exit gate:** the second incident improves at least two agreed metrics and the complete six-minute story is visible without inspecting logs manually.

## Phase 7 — Hardening and rehearsal

**Outcome:** the demo is deterministic, portable, and recoverable.

- Verify all acceptance criteria.
- Test timeouts, bounded retries, invalid model JSON, provider outages, and security-gateway failure.
- Validate no-change, small-investigation, full-investigation, proposal, and dashboard latency targets.
- Benchmark serial versus concurrent Council execution.
- Rehearse startup, reset, replay, failure recovery, and degraded mode.
- Pin versions and document reproducible GPU launch configuration.
- Package with NemoClaw where the event environment supports it.

**Exit gate:** repeated clean resets produce the same end-to-end result without manual code changes.

## Priority boundary

### P0 — Build before anything else

- Heartbeat and durable state
- Complete Redis incident flow
- Main Nemotron planning and synthesis
- Three structured Council domains, with at least two using real smaller-model work
- Deterministic metrics and deployment tools
- Real vLLM inference and concurrency
- Episodic memory, candidate skill, and replay validation
- First-versus-learned measurement
- HiddenLayer on critical boundaries
- OpenShell policy and tested denials
- Deterministic reset and judge-facing dashboard

### P1 — Add after P0 is repeatable

- Full Historian reranking
- Richer Security specialist behavior
- Independent critic improvements
- Runbook proposal workflow
- Trust-score integration
- Human approval workflow
- Model-tier cost comparison
- NemoClaw packaging

### P2 — Defer until after the submission core

- Live Kubernetes integration
- Draft pull request creation
- External notifications
- Additional incident families
- Larger replay suite
- Hosted escalation model
- Broader dependency-graph analysis
