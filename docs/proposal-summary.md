# Athena proposal summary

Related design references: [architecture diagrams](architecture.md) and [API and endpoint map](api-endpoints.md).

## Product objective

Athena is a persistent SRE control plane that wakes on a heartbeat, decides whether operational action is required, performs bounded evidence-driven investigations, and learns reusable procedures from verified outcomes.

The product is not intended to replace monitoring platforms or automatically mutate production. Its value comes from coordinating existing evidence sources, focusing model capacity on the right questions, retaining verified operational knowledge, and remaining constrained by external security controls.

## Target users

- **Platform engineers and SREs:** faster triage, reliable dependency context, actionable recommendations, and reduced repeated investigation.
- **Security engineers:** inspection of model and tool interactions, poisoned-memory prevention, least-privilege execution, and auditable security decisions.
- **Engineering managers:** concise operational summaries, visible risk, and measurable investigation improvement.
- **Application engineers:** service-impact explanations, current runbook guidance, and ownership information.

## Core operating loop

1. Wake on a periodic or manual heartbeat.
2. Load durable state and incomplete work.
3. Collect changed signals, artifacts, security events, and pending tasks.
4. Inspect untrusted inputs through HiddenLayer.
5. Choose `WAIT`, `INVESTIGATE`, `ESCALATE`, `CONTINUE`, `RETRY`, `LEARN`, `REQUEST_APPROVAL`, or `CONTAIN`.
6. Build a bounded investigation plan with tools, specialists, budgets, evidence requirements, and stop conditions.
7. Run deterministic evidence tools and the Observability specialist concurrently as the first round of investigation.
8. Evaluate mid-cycle whether Observability findings change the Platform specialist's scope; revise the Platform question if the hypothesis has shifted (REVISE_PLAN) or confirm it is unchanged (CONTINUE_PLAN).
9. Run the Platform specialist in the second round with the confirmed or revised question.
10. Inspect model and tool interactions at each trust boundary.
11. Use Nemotron-Super to correlate evidence, resolve contradictions, run an adversarial self-critique, and produce one accountable decision.
12. Store the verified episode and its outcome, including the mid-cycle revision path.
13. Draft a candidate skill encoding the winning hypothesis chain and a reviewable runbook proposal.
14. Scan, replay, and validate learned material before promotion.
15. Reuse validated knowledge on later heartbeats and measure improvement.

## Architecture responsibilities

### Athena

Athena owns heartbeat state, planning, routing, budgets, synthesis, confidence, escalation, learning, approvals, and final persistence. Athena cannot bypass security controls, invent evidence, mutate production, access protected credentials, or promote unvalidated knowledge.

### Council

Council members are bounded workers with no persistent goals or final decision authority. Observability and Platform are implemented as Nemotron-Nano specialist models for the hackathon. Historian, Security, and Critic are deferred to Part Two; their functions are absorbed by Nemotron-Super for the P0 MVP.

- **Observability [P0]:** timelines, baselines, symptoms, and evidence-supported hypotheses. Runs in round one.
- **Platform [P0]:** Kubernetes and deployment findings, configuration evidence, and safe verification actions. Runs in round two with a question refined by mid-cycle evaluation.
- **Security [P2]:** operational interpretation of HiddenLayer and OpenShell events. MVP: Nemotron-Super reads HiddenLayer findings directly.
- **Historian [P2]:** retrieval and comparison of validated episodes and promoted skills. MVP: deterministic `search_skills` and `search_episodes` tools with Nemotron-Super comparison during synthesis.
- **Critic [P2 as standalone]:** contradiction discovery, unsupported-claim detection, and bounded follow-up requests. MVP: Nemotron-Super adversarial self-critique prompt after synthesis.

### Deterministic tools

Metrics queries, log retrieval, Kubernetes reads, manifest diffs, dependency traversal, blast-radius calculation, network checks, schema validation, secret detection, hashing, policy checks, and skill replay should remain deterministic wherever practical.

## Model strategy

- Use deterministic execution when a reliable non-model answer exists.
- Use smaller models for narrow evidence interpretation.
- Use the main Nemotron model for planning, routing, cross-domain synthesis, contradiction resolution, final judgment, and learning.
- Serve real inference through OpenAI-compatible vLLM endpoints.
- Run independent Council requests concurrently and record model, role, latency, and token metrics.

## Security model

Athena treats users, telemetry, repository content, runbooks, tool results, model responses, generated content, and stored memory as untrusted.

- **HiddenLayer** decides whether an interaction should be trusted. It can redact, exclude, quarantine, block, reduce source trust, create a security event, or reject persistence.
- **OpenShell** decides whether an action can cross a capability boundary. It allows approved reads and agent-owned writes while denying protected credentials, arbitrary egress, production mutation, destructive commands, and security-policy modification.

Persistent writes and actionable tool calls fail closed when required security inspection is unavailable.

## MVP demo

The demo uses three linked scenarios:

1. **Unknown Redis pattern:** a deployment changes connection-pool behavior; Athena detects API errors and Redis rejected connections, identifies saturation, and creates a validated skill.
2. **Related pattern:** another service exhibits a similar failure; Athena reuses the skill and demonstrates fewer calls, lower latency, or higher confidence.
3. **Security attack:** a poisoned runbook requests credential theft and exfiltration; HiddenLayer quarantines it and OpenShell denies both protected access and unapproved egress.

## Definition of success

The MVP is successful when it demonstrates autonomous heartbeat operation, durable recovery, real vLLM inference, central Nemotron reasoning, evidence-backed Council work, recursive skill creation, replay-based promotion, measurable improvement, poisoned-memory prevention, and externally enforced capability boundaries.

## Requirements requiring an explicit project decision

1. **Council count:** FR-5 requires three Council domains, while the P0 and acceptance sections mention two smaller specialists. The implementation backlog uses the stricter three-domain requirement.
2. **Improvement threshold:** FR-14 requires improvement in at least two metrics but provides no minimum. The team should set target thresholds before performance work begins.
3. **Outcome verification:** the proposal requires a verified incident outcome before learning but does not define the authority. Fixtures can provide demo ground truth; human or trusted-system approval should be the production path.
4. **Heartbeat cadence:** periodic operation is required, but cadence is unspecified and should be configurable.
5. **Persistence technology:** restart survival is required, but the storage engine is intentionally open. Select the simplest durable implementation compatible with recovery and replay.
