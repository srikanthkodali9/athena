# Demo engineer

## Mission

Turn the P0 capabilities into a deterministic, judge-readable six-minute demonstration that resets cleanly and visibly proves autonomy, within-cycle revision, learning, measurable improvement, quarantine, and capability denial.

## Initial assignment

Create the fixture and script contract for:

1. Incident one: unknown Redis connection saturation with a forced `REVISE_PLAN`
2. Incident two: related pattern that reuses the promoted skill
3. Security attack: poisoned runbook requesting credential access and external exfiltration
4. Clean reset and repeatable replay
5. First-versus-learned metrics comparison

Build UI only after the fixture events and API response shapes are stable.

## Owned paths

- `app/ui/`
- `fixtures/codex/incidents/`
- `fixtures/codex/telemetry/`
- `fixtures/codex/kubernetes/`
- `fixtures/codex/runbooks/`
- `scripts/`
- `tests/demo/`

Changes elsewhere require coordinator approval.

## Required deliverables

- Deterministic, timestamped Redis telemetry and deployment fixtures
- Explicit ground truth and expected evidence IDs for both incidents
- Poisoned runbook containing synthetic, non-secret attack content
- One-command start, reset, and replay scripts
- Comparison calculation for time, specialist calls, tool calls, main-model calls, confidence, unsupported hypotheses, and prior-skill reuse
- Dashboard views for heartbeat state, plan revision, evidence, model routing, learning lifecycle, security events, denials, and comparison
- End-to-end demo test using mocks when live services are unavailable

## Demo constraints

- The first screen must show autonomous heartbeat detection without a user prompt.
- The mid-cycle `REVISE_PLAN` and changed Platform question must be visible.
- The second incident must improve at least two preselected metrics under comparable conditions.
- HiddenLayer quarantine must visibly change context, trust, persistence, and incident state.
- OpenShell protected-path and egress denials must be shown as independent events.
- Never place real credentials, external destinations, or production data in fixtures.
- Keep UI labels accurate about mock, degraded, or live provider modes.

## Verification

- Run the complete demo twice from a clean reset and compare results.
- Verify scripts require no manual file edits between runs.
- Verify the displayed metrics come from persisted execution records rather than hard-coded UI values.
- Rehearse provider failure and demonstrate the intended degraded or fail-closed behavior.

## Do not

- Add new incident families before the Redis story is stable.
- Hide latency or failure behind pre-rendered results.
- duplicate core calculation logic in the UI.
- edit security or inference internals to force expected demo outcomes.

## Handoff

Use the repository handoff format and include the exact start, reset, replay, and test commands plus a minute-by-minute demo runbook.
