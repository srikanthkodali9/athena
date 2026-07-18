# Athena development sub-agents

These definitions are reusable task briefs for development sub-agents working on this repository. They are not runtime Athena Council agents.

## Roster

| Agent ID | Focus | Definition | Initial wave |
|---|---|---|---:|
| `architecture_expert` | Decisions, boundaries, contracts, requirement reconciliation | [architecture_expert.md](architecture_expert.md) | 1 |
| `core_developer` | API, heartbeat, orchestration, evidence, persistence | [core_developer.md](core_developer.md) | 1 |
| `security_expert` | HiddenLayer boundary, OpenShell policy, adversarial tests | [security_expert.md](security_expert.md) | 1 |
| `reviewer` | Independent review, acceptance traceability, test verification | [reviewer.md](reviewer.md) | 2 |
| `inference_engineer` | vLLM adapters, routing, prompts, Council contracts | [inference_engineer.md](inference_engineer.md) | 2 |
| `recursive_learning_expert` | Episodes, candidate skills, negative knowledge, replay | [recursive_learning_expert.md](recursive_learning_expert.md) | 3 |
| `demo_engineer` | Fixtures, scripts, metrics comparison, dashboard | [demo_engineer.md](demo_engineer.md) | 3 |

Machine-readable ownership and scheduling metadata is in [manifest.yaml](manifest.yaml).

## Concurrency plan

The root coordinator occupies one of four available slots, so at most three sub-agents run concurrently.

### Wave 1 — Foundation

- `architecture_expert`
- `core_developer`
- `security_expert`

The architect locks decisions while the developer creates a minimal scaffold and the security expert builds interfaces, policies, and attack fixtures. The developer must avoid decisions still marked as blocking by the architect.

### Wave 2 — Investigation engine

- `core_developer`
- `inference_engineer`
- `reviewer`

The reviewer evaluates Wave 1 before large integration changes accumulate.

### Wave 3 — Recursive demo

- `recursive_learning_expert`
- `demo_engineer`
- `security_expert`

This wave connects verified outcomes, protected learning, replay validation, improvement measurement, and the poisoned-runbook story.

### Wave 4 — Stabilization

- `reviewer`
- One targeted implementation agent selected from the review findings
- `demo_engineer`

No feature expansion is allowed during stabilization unless it closes a P0 acceptance gap.

## How to assign work

Give each agent one bounded task copied from its definition. Include:

- the goal and acceptance check;
- the exact owned paths it may edit;
- relevant FR and task IDs;
- required test commands;
- current dependencies or mocks;
- a reminder to follow `AGENTS.md` and use the required handoff format.

Example assignment:

```text
Follow AGENTS.md and .agents/core_developer.md.

Implement FND-01 and the minimum of FND-03 needed for a runnable health endpoint.
Edit only the core developer ownership paths.
Do not implement live inference or security vendors; use typed ports for both.
Add focused tests and return the standard handoff.
```

## Coordination rules

- Definitions describe ownership, not permanent branches or autonomous goals.
- The coordinator chooses the task and decides when an agent may expand scope.
- Reviewer findings are advisory until the coordinator prioritizes them.
- An agent that finishes early should stop and hand off; it should not pull unassigned P1 or P2 work.
- Agents share the same filesystem, so non-overlapping write scopes are mandatory.
