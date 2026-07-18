# Athena

Athena is a heartbeat-driven, self-improving SRE intelligence. It autonomously detects operational changes, builds bounded investigations, coordinates specialist models and deterministic tools, and converts verified incident outcomes into validated SRE skills and reviewable runbook proposals.

The project is being prepared for the AITX Community × NVIDIA Claw Agent Hackathon, with Recursive Intelligence as its primary track and HiddenLayer Runtime Security as an additional target.

## Project status

The repository is currently in the planning and documentation stage. Implementation should begin with the P0 end-to-end demo path rather than isolated platform components.

## Documentation

- [Full product proposal](Athena_SRE_Agent_PRD_and_Build_Plan.md)
- [Proposal summary](docs/proposal-summary.md)
- [Architecture diagrams](docs/architecture.md)
- [API and endpoint map](docs/api-endpoints.md)
- [Functional-requirement backlog](docs/functional-requirements.md)
- [Implementation roadmap](docs/implementation-roadmap.md)
- [Development sub-agent roster](.agents/README.md)
- [Synthetic demo datasets](fixtures/README.md)

## MVP story

The first release must demonstrate one continuous story:

1. A heartbeat detects a Redis connection-saturation incident without a user prompt.
2. Athena uses deterministic evidence and bounded Council specialists to identify the cause.
3. Nemotron performs planning, contradiction resolution, and final synthesis.
4. Athena records the verified episode and creates a candidate diagnostic skill.
5. HiddenLayer scans the learning artifact before it is persisted.
6. Replay validation promotes the skill.
7. A related incident reuses the skill and improves at least two measured metrics.
8. A poisoned runbook is quarantined by HiddenLayer.
9. OpenShell denies protected credential access and unapproved network egress.

## Engineering principles

- Athena is the only persistent autonomous decision-maker.
- Deterministic tools establish facts; models interpret and synthesize them.
- Every operational claim must cite an evidence ID.
- Specialist tasks are narrow, structured, budgeted, and least-privileged.
- Untrusted content is inspected before model consumption or persistence.
- Learned material is versioned and replay-validated before promotion.
- Human-owned runbooks are never silently overwritten.
- High-risk operations remain denied or approval-gated.

## Initial delivery target

The first milestone is a deterministic, resettable local demo with:

- Heartbeat scheduling and durable state
- Redis incident fixtures
- Metrics and deployment-diff tools
- Nemotron planning and synthesis through vLLM
- Three structured Council domains
- Skill creation and replay validation
- HiddenLayer coverage on critical trust boundaries
- OpenShell allow, deny, and approval-gated policies
- A dashboard showing incident evidence, learning, security actions, and first-versus-learned metrics
