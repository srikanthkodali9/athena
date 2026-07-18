# Inference engineer

## Mission

Implement the model-serving and reasoning layer that proves Athena uses model capacity intentionally: Nemotron-Super for strategy and Nemotron-Nano for bounded specialist interpretation through OpenAI-compatible vLLM endpoints.

## Initial assignment

Build mockable contracts and prompt versions for:

1. Nemotron-Super initial planning
2. Nemotron-Nano Observability interpretation
3. Nemotron-Super mid-cycle `CONTINUE_PLAN` or `REVISE_PLAN`
4. Nemotron-Nano Platform interpretation with revised scope
5. Nemotron-Super synthesis
6. Nemotron-Super adversarial self-critique

Connect live vLLM only after deterministic provider tests pass.

## Owned paths

- `app/models/`
- `app/routing/`
- `app/council/`
- `prompts/`
- `tests/inference/`

Changes elsewhere require coordinator approval.

## Required deliverables

- OpenAI-compatible provider port with mock and vLLM adapters
- Explicit main and specialist model configuration
- Versioned prompts and strict response schemas for all six P0 calls
- Routing records containing task, role, model ID, reason, tokens, latency, and degraded-mode status
- One repair retry for invalid structured output
- Timeouts, bounded retries, health checks, and explicit fallback behavior
- Tests for routing, prompt evidence restrictions, schema rejection, fallback, metrics, and one-plan-revision limit

## Inference constraints

- Observability and deterministic tools run concurrently in Round 1.
- Platform runs only after mid-cycle evaluation; do not parallelize the two specialists in P0.
- Specialists receive one narrow question and a minimal allowlisted evidence bundle.
- Specialists cannot recommend final remediation or invoke arbitrary tools.
- The Critic role uses a separate adversarial Nemotron-Super prompt in P0.
- Degraded mode cannot emit high-risk recommendations without approval.
- All model inputs and outputs use the security gateway supplied by the security owner.

## Verification

- Test every structured-output schema with valid and invalid fixtures.
- Test evidence-ID allowlisting and missing-evidence behavior.
- Measure mock-call sequencing to prove Platform starts after mid-cycle evaluation.
- When live vLLM is available, record time to first token, total latency, token counts, and throughput.

## Do not

- Implement standalone Historian, Security, or Critic agents for P0.
- Put vendor-specific code in orchestration or Council contracts.
- Send full memory archives or unrestricted repository content to a model.
- claim live-model performance based on mock or fallback results.

## Handoff

Use the repository handoff format and include the exact provider configuration, prompt versions, sequence guarantees, and integration ports.
