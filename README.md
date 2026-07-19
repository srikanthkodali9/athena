# Athena

Athena is a heartbeat-driven, self-improving SRE intelligence. It detects
operational changes, runs bounded investigations with specialist models and
deterministic tools, and turns verified incident outcomes into replay-validated
SRE skills.

The P0 demonstration follows one Redis connection-saturation incident family:

1. Incident one triggers a mid-cycle `REVISE_PLAN` decision.
2. Athena records the verified episode and promotes a security-scanned skill
   after deterministic replay validation.
3. Incident two reuses that skill and completes with fewer model and specialist
   calls.
4. A poisoned runbook is quarantined, while OpenShell independently denies
   protected credential access and unapproved egress.

## Project status

The repository contains a runnable, deterministic mock demonstration with a
FastAPI service, SQLite state, Redis fixtures, recursive learning, security
checks, comparison metrics, and a judge-facing dashboard. Optional live adapters
connect the strategic and Council model tiers to OpenAI-compatible NVIDIA
NIM/vLLM endpoints and runtime inspection to HiddenLayer.

## Prerequisites

- macOS or Linux
- Git, Bash, and `curl`
- Python 3.12
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)

Install `uv` with Homebrew:

```bash
brew install uv
```

Or use Astral's standalone installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart the shell if necessary, then verify the installation:

```bash
uv --version
```

## Setup

From the repository root, install Python 3.12 and synchronize the locked
development environment:

```bash
uv python install 3.12
uv venv --python 3.12
uv sync --locked
```

`uv sync` creates the ignored `.venv/`, installs Athena in editable mode, and
installs the default development dependency group used by the tests.

The deterministic mock demo does not require an `.env` file or any credentials.

## Run the deterministic demo

Start Athena from the repository root:

```bash
./scripts/start_demo.sh
```

The start script selects mock inference and mock security unless explicitly
overridden, resets the SQLite demo state, and starts the dashboard at:

- Dashboard: <http://127.0.0.1:8000/dashboard/>
- API: <http://127.0.0.1:8000/api/v1>
- Interactive API documentation: <http://127.0.0.1:8000/docs>

Use one of the following ways to run the story:

- Open the dashboard and select **Run both demos**. This one button runs the
  standard `fixtures/incidents` story and the isolated `fixtures/codex`
  workflow, then shows both result sets in separate dashboard sections.
- From a second terminal, run the command-line replay:

  ```bash
  ./scripts/run_replay.sh
  ```

The replay runs both Redis incidents, prints the persisted first-versus-learned
comparison, and shows the sanitized HiddenLayer and OpenShell decisions.

Stop the server with `Ctrl+C`.

## Run the separate Codex dataset workflow

The datasets under `fixtures/codex/` have their own isolated workflow. It does
not import the existing `app.tools.fixtures` registry, does not write to the
normal demo SQLite state, and never treats `expected-outcome.json` as runtime
evidence.

Run it directly without starting a server:

```bash
./scripts/run_codex_demo.sh
```

Print the complete result as JSON:

```bash
./scripts/run_codex_demo.sh --json
```

The workflow runs the two Codex Redis scenarios, promotes and reuses the
ephemeral replay-validated skill, exercises the Codex security datasets, checks
all eight edge cases, and enforces the required two-metric improvement gate.

The dashboard's **Run both demos** button includes this workflow automatically
and displays it below the standard heartbeat panels. To run only the Codex
workflow while the dashboard server is running, use its dedicated endpoint:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/demo/codex/run \
  | python3 -m json.tool
```

`./scripts/run_replay.sh` continues to use only the original fixture path. The
combined dashboard action does not mix Codex results into the original cycles,
learning store, comparison, or security ledger.

## Run the API without the demo dashboard

To start only the core Athena API in deterministic mode:

```bash
ATHENA_ENV=development \
ATHENA_INFERENCE_MODE=mock \
ATHENA_SECURITY_MODE=mock \
uv run athena
```

Trigger a heartbeat manually:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/heartbeats \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: local-redis-001' \
  -d '{"trigger":"manual","fixture":"redis-incident-001","reason":"local demo"}' \
  | python3 -m json.tool
```

The response contains the `cycle_id` used by the heartbeat and metrics read
routes.

## Reset and replay

Stop the running server before deleting its state, then run:

```bash
./scripts/reset_demo.sh
```

This removes only generated SQLite runtime state under `.athena/`. It does not
modify source code, fixtures, configuration, or human-owned runbooks. Starting
with `./scripts/start_demo.sh` performs this reset automatically.

## Configure live providers

Live mode is optional. It requires reachable OpenAI-compatible strategic and
Council model endpoints plus valid HiddenLayer credentials.

Install the live HiddenLayer dependency:

```bash
uv sync --locked --extra live
```

Create a local configuration from the committed placeholder template:

```bash
cp .env.example .env
```

Edit `.env` locally and provide the required model endpoint URLs, model IDs,
API keys when applicable, and HiddenLayer credentials. Do not enable live mode
until those values are complete.

Start the live-provider path explicitly:

```bash
ATHENA_INFERENCE_MODE=live \
ATHENA_SECURITY_MODE=live \
./scripts/start_demo.sh
```

Check sanitized readiness without exposing credentials:

```bash
curl -sS http://127.0.0.1:8000/api/v1/ready | python3 -m json.tool
curl -sS http://127.0.0.1:8000/api/v1/config/status | python3 -m json.tool
```

## Protect local secrets

`.env`, `.env.*`, private keys, local databases, logs, and generated runtime
state are excluded by `.gitignore`. `.env.example` is the only environment file
that should be committed, and it must contain placeholders only.

After creating `.env`, confirm Git ignores it:

```bash
git check-ignore -v .env
```

Safety rules:

- Never commit, paste, print, upload, or attach `.env`.
- Never copy real values into `.env.example`.
- Use a secret manager or protected environment variables in shared and CI
  environments.
- Review staged changes with `git diff --cached` before every commit.
- If a credential is ever committed or shared, rotate it immediately; adding
  the file to `.gitignore` does not remove it from Git history.

The status APIs intentionally expose only boolean configuration state and
sanitized adapter metadata, never credentials or full secret-bearing URLs.

## Tests and validation

Run the complete test suite:

```bash
uv run pytest -q
```

Validate the synthetic fixture catalog:

```bash
uv run python scripts/validate_fixtures.py
```

Run only the Codex workflow tests:

```bash
uv run pytest -q tests/demo/test_codex_demo_workflow.py
```

Run the first-versus-learned improvement gate in process:

```bash
uv run python scripts/demo_eval.py
```

Live smoke tests remain skipped unless the corresponding endpoints and
credentials are explicitly configured.

## Documentation

- [Full product proposal](Athena_SRE_Agent_PRD_and_Build_Plan.md)
- [Proposal summary](docs/proposal-summary.md)
- [Architecture diagrams](docs/architecture.md)
- [Accepted architecture decisions](docs/adr/)
- [API and endpoint map](docs/api-endpoints.md)
- [Functional-requirement backlog](docs/functional-requirements.md)
- [Implementation roadmap](docs/implementation-roadmap.md)
- [Synthetic demo datasets](fixtures/codex/README.md)

## Engineering principles

- Athena is the only persistent autonomous decision-maker.
- Deterministic tools establish facts; models interpret and synthesize them.
- Every operational claim must cite an evidence ID.
- Specialist tasks are narrow, structured, budgeted, and least-privileged.
- Untrusted content is inspected before model consumption or persistence.
- Learned material is versioned and replay-validated before promotion.
- Human-owned runbooks are never silently overwritten.
- High-risk operations remain denied or approval-gated.
