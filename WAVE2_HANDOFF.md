# Wave 2 Handoff — Athena

**Branch:** `feat/wave1-foundation` · **PR:** [#2](https://github.com/srikanthkodali9/athena/pull/2)
**Commits:** `fbf83dc` (Wave 1) · `6cfe754` (Wave 2)
**Test status:** 143 passed, 2 skipped (guarded live smokes) — `uv run pytest -q`
**Runtime default:** deterministic mock mode; live NVIDIA NIM Nemotron + HiddenLayer engage via git-ignored `.env`.

---

## 1. What is complete (Phases 1–4)

### Wave 1 — Foundation
- **Architecture:** ADR-0001 (stack), ADR-0002 (SQLite state + recovery), ADR-0003 (two-round investigation contract), contract inventory.
- **Core spine:** FastAPI app, typed settings, Pydantic v2 domain schemas, heartbeat lifecycle (manual + periodic, overlap/idempotency, no-change WAIT path with zero model calls), durable SQLite cycle store with transactional checkpoint + restart recovery, deterministic tool registry (`query_metrics`, `diff_deployment`).
- **Security:** canonical `SecurityGateway` contract, `HiddenLayerGateway` (local-mock + live adapters), OpenShell policy engine + `policies/openshell.yaml`, source-trust, sanitized events, poisoned-runbook/credential/egress attack fixtures.
- **HiddenLayer:** live adapter uses the official `hiddenlayer` Python SDK with OAuth2 client_id/client_secret (optional `[live]` extra).

### Wave 2 — Investigation engine
- **Inference:** `NimChatClient` (streaming, reasoning-trace aware, TTFT + usage), `TieredInferenceRouter` implementing `InferencePort` (strategic/midcycle/critic → Nemotron-Super, specialist → Nemotron-Nano), structured JSON + one repair retry, per-call metrics, degraded fallback.
- **Council:** Observability + Platform specialists (narrow contracts, evidence-ID allowlist enforcement, confidence rubric, missing-evidence), concurrency helper; versioned prompt templates (council + athena plan/midcycle/synthesis/critic).
- **Core live wiring:** live-mode config bridge (`.env` now engages live security + inference), inference-mode seam, `ChatResult.ttft_ms`/`routing_reason`, per-inference metrics persisted; `/metrics/cycles`, `/security/events`, `/security/denials` routes; readiness reports live/mock + adapter.
- **Security mapping:** HiddenLayer detector→verdict mapping rebuilt from **real** `interactions.analyze` responses — gates exfiltration on the `url` detector, avoids the benign `prompt_injection` false-positive quarantine, keeps local secret redaction (HiddenLayer does not detect generic API keys/passwords).

### Verified live (real Nemotron + HiddenLayer)
- Correct tier routing (super for strategic/midcycle/critic, nano for specialist) with `routing_reason`, real TTFT/latency captured.
- Full two-round investigation → mid-cycle `CONTINUE_PLAN` → synthesis → critic → `ESCALATE`.
- Platform specialist returns evidence-backed JSON (conf 0.85–0.92, cites all 3 evidence IDs, correctly identifies the Redis pool 10× change).
- 11 live HiddenLayer inspections per cycle; benign content correctly **not** quarantined (validates the mapping fix in the live path).

---

## 2. Known issues found during live verification (fix before demo)

| # | Severity | Issue | Owner |
|---|---|---|---|
| 1 | 🔴 blocker | **Live cycle hangs** — a round-2 NIM streaming call froze with no timeout firing; cycle stalled at 3/6 calls for 5+ min. No enforced per-call streaming timeout, and the orchestrator does not enforce `deadline_seconds`. | inference + core |
| 2 | 🟠 | **`/ready` = not_ready** — gateway `health()` returns `false` even though live HiddenLayer inspections succeed. Misleading for judges. | security |
| 3 | 🟠 | **Slow** — hosted NIM calls run 20–34s each (nano reasoning ~1000+ tokens); a full cycle is minutes, over the <90s NFR target. | inference |
| 4 | 🟠 | **Observability specialist reliability** — intermittent empty/degraded finding when the 9B model emits an unexpected key and `extra="forbid"` fails the whole output (repair-retry fix was in progress). | inference |

**Fix plan:** enforce a hard per-call timeout that fires on streaming responses (inference); enforce the investigation `deadline_seconds` with a fail-safe degraded decision (core); make gateway `health()` reflect real reachability (security); tighten reasoning budgets for speed (inference).

---

## 3. Pending work (implementation plan)

### P0 — required for a credible submission

**Phase 5 — Recursive learning (the primary track; not started)**
- FR-7: memory layers (working / episodic / semantic / pattern / procedural) — only raw cycle persistence exists.
- FR-8: recursive skill writing — candidate skills, versioned skill schema, `known_false_leads`, observed→candidate→scanned→shadow→validated→promoted lifecycle, HiddenLayer scan-before-persist, **replay validation**.
- FR-9: runbook improvement — reviewable proposals without overwriting source.
- FND-04: incident-two (related pattern) + poisoned-runbook fixtures (only incident-one + attack fixtures exist).

**Phase 6 — Improvement proof + dashboard (not started)**
- FR-14: measurable improvement — incident-one baseline vs incident-two, metric deltas, fail-if-<2-metrics-improve.
- Judge-facing dashboard (`app/ui/`) — nothing built.
- Poisoned-runbook live demo end-to-end (quarantine + OpenShell denials visible).

**Phase 7 — Hardening (not started)**
- `scripts/`: `start_demo.sh`, `reset_demo.sh`, `run_replay.sh` (deterministic reset/replay).
- FR-11 enforcement wiring — OpenShell `evaluate()` is built but not wired into the tool-execution path, so `/security/denials` stays empty.
- TST-05/06/07 — performance tests, deterministic reset, acceptance-criteria mapping.

### P1 — after P0 is repeatable
- Runbook patch proposal workflow · model-tier cost comparison · HiddenLayer trust-score integration (partial — trust store exists) · human approval workflow · NemoClaw packaging.

### P2 — Part Two (deferred; do not start until P0 stable)
- Historian specialist (embedding retrieval/reranking) · standalone Security specialist · standalone Critic agent · live Kubernetes · draft-PR creation · notifications · additional incident families.

---

## 4. How to run / verify

**Fast path (deterministic, no GPU):**
```bash
uv run pytest -q            # 143 pass
```

**Live path (real Nemotron + HiddenLayer):**
```bash
# install live extra + set .env creds (git-ignored), then:
ATHENA_PORT=8077 uv run athena
B=http://127.0.0.1:8077/api/v1
curl -s $B/config/status | python3 -m json.tool
curl -s -X POST $B/heartbeats -H 'Content-Type: application/json' \
     -H 'Idempotency-Key: t1' -d '{"trigger":"manual","fixture":"redis-incident-001"}'
curl -s $B/heartbeats/<cycle_id>
curl -s $B/metrics/cycles/<cycle_id> | python3 -m json.tool
curl -s $B/security/events | python3 -m json.tool
```
> Note: the live cycle currently can hang (issue #1) — fix before relying on the live path for a demo.

Secrets live only in git-ignored `.env`; `.env.example` is the template.

---

## 5. Recommended next step

1. Fix the four live-reliability issues above (timeouts + deadline enforcement + health + speed).
2. Then Wave 3: recursive learning (FR-7/8/9) → improvement proof (FR-14) → dashboard → demo scripts.

A hanging cycle undermines any learning demo built on top of it, so the reliability fixes come first.
