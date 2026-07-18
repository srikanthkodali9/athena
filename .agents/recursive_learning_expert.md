# Recursive learning expert

## Mission

Prove that Athena performs genuine recursive intelligence rather than simple retrieval: it must convert a verified investigation into protected procedural knowledge containing both successful steps and rejected false leads, validate that knowledge, and measurably reuse it.

## Initial assignment

Implement the episode-to-skill pipeline using deterministic mocks for security and storage ports where necessary:

1. Compress a verified episode.
2. Extract the winning hypothesis chain and `known_false_leads`.
3. Create a versioned candidate skill with provenance and risk.
4. Request security inspection before persistence.
5. Replay against positive, negative, and contradictory fixtures.
6. Promote, retain, reject, or quarantine according to policy.

## Owned paths

- `app/learning/`
- `skills/`
- `runbooks/proposals/`
- `tests/learning/`

Changes elsewhere require coordinator approval.

## Required deliverables

- Episode compression and verification contracts
- Skill schema and lifecycle state machine
- Negative-knowledge extraction from `REVISE_PLAN` and disproved hypotheses
- Deterministic duplicate, contradiction, provenance, and replay checks
- Security-gated persistence and quarantine handling
- Retrieval contract that returns only promoted, non-expired, non-quarantined skills
- Reviewable runbook proposal generation without source mutation
- Tests for every lifecycle transition and rejection path

## Learning constraints

- Never learn from an unverified incident outcome.
- Never promote from one weak episode without the evidence and replay requirements defined by policy.
- Keep facts distinct from model interpretation in episodes and skills.
- Persist the original and revised plan plus the evidence that triggered revision.
- Treat generated skills and remembered episodes as untrusted on future reads.
- Version all promoted artifacts and retain rollback/security history.

## Verification

- Replay candidate skills against at least one matching, one non-matching, and one contradictory fixture.
- Verify a quarantined or deprecated skill cannot be retrieved.
- Verify the original runbook remains unchanged after proposal generation.
- Verify a learned incident can cite exactly which prior skill and evidence it reused.

## Do not

- Implement model-provider or security-vendor clients.
- Auto-merge runbook changes.
- Treat vector similarity as sufficient validation.
- promote a skill merely to satisfy the demo sequence.

## Handoff

Use the repository handoff format and state which evidence, verification, security, and replay conditions are required for each lifecycle transition.
