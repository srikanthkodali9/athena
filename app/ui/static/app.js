"use strict";

const API = "/api/v1";
const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
  );
}
function pill(text, kind) { return `<span class="pill ${kind || "info"}">${esc(text)}</span>`; }

async function getJSON(path) {
  const r = await fetch(API + path, { headers: { Accept: "application/json" } });
  return { ok: r.ok, status: r.status, body: await r.json().catch(() => ({})) };
}

// --- panels ---------------------------------------------------------------

function renderMode(mode) {
  const badge = $("mode-badge");
  if (!mode) { badge.textContent = "mode: ?"; return; }
  const inf = mode.inference_mode, sec = mode.security_mode;
  const degraded = mode.degraded_mode;
  badge.textContent = `inference: ${inf} · security: ${sec}`;
  badge.className = "badge " + (inf === "live" && sec === "live" ? "badge-live" : "badge-mock");
  $("footer-note").textContent =
    `env=${mode.env} · inference=${inf} · security=${sec} · ` +
    (degraded ? "DEGRADED/MOCK mode — values are deterministic" : "LIVE providers in path") +
    " · all metrics read from persisted records";
}

function renderAutonomy(c) {
  const el = $("autonomy-body");
  if (!c) { el.innerHTML = `<span class="muted">Not run yet.</span>`; return; }
  const change = (c.changes_detected && c.changes_detected[0]) || {};
  el.innerHTML = `
    <dl class="kv">
      <dt>trigger</dt><dd>${pill(c.trigger, "info")} <span class="muted">(no user prompt)</span></dd>
      <dt>fixture</dt><dd class="mono">${esc(c.fixture)}</dd>
      <dt>state</dt><dd>${pill(c.state, c.state === "completed" ? "good" : "warn")}</dd>
      <dt>change class</dt><dd>${pill(c.change_class || "—", "info")}</dd>
      <dt>signal</dt><dd>${esc(change.summary || "—")}</dd>
      <dt>decision</dt><dd>${pill(c.decision || "—", c.decision === "ESCALATE" ? "good" : "info")}</dd>
    </dl>`;
}

function renderRevision(c) {
  const el = $("revision-body");
  if (!c || !c.plan) { el.innerHTML = `<span class="muted">Not run yet.</span>`; return; }
  const p = c.plan;
  const revised = p.revision_used || c.mid_cycle_decision === "REVISE_PLAN";
  const orig = p.original_platform_question;
  const cur = p.platform_question;
  let html = `<div>mid-cycle: ${pill(c.mid_cycle_decision || "—", revised ? "good" : "info")}</div>`;
  if (revised && orig) {
    html += `
      <div class="qbox"><div class="lbl">original Platform question</div>
        <div class="strike">${esc(orig)}</div></div>
      <div class="qbox"><div class="lbl">revised Platform question →</div>
        <div>${esc(cur)}</div></div>
      <div class="note">reason: ${esc(p.revision_reason || "")}</div>`;
  } else {
    html += `
      <div class="qbox"><div class="lbl">Platform question</div><div>${esc(cur)}</div></div>
      <div class="note">This cycle recorded <strong>${esc(c.mid_cycle_decision || "—")}</strong>.
      Mid-cycle <strong>REVISE_PLAN</strong> for the Redis-saturation evidence requires the
      evidence-driven <code>_step_mid_cycle</code> patch flagged in <code>fixtures/README.md</code>;
      this panel reads <code>mid_cycle_decision</code> / plan questions straight from the
      persisted cycle and will show the from→to change the moment that lands.</div>`;
  }
  el.innerHTML = html;
}

function renderEvidence(c) {
  const el = $("evidence-body");
  if (!c) { el.innerHTML = `<span class="muted">Not run yet.</span>`; return; }
  const ids = c.evidence_ids || [];
  el.innerHTML =
    `<div>${ids.length} registered fact(s):</div>` +
    `<div style="margin-top:6px">${ids.map((i) => `<span class="evid">${esc(i)}</span>`).join(" ") || "—"}</div>` +
    `<div class="note">Deterministic evidence with provenance; interpretations are kept separate as findings.</div>`;
}

function renderRouting(c) {
  const el = $("routing-body");
  if (!c) { el.innerHTML = `<span class="muted">Not run yet.</span>`; return; }
  const m = c.metrics || {};
  const infs = m.inferences || [];
  const rows = infs.map((i) => `
    <tr><td>${esc(i.role)}</td><td class="mono">${esc(i.model_id || "—")}</td>
    <td>${esc(i.routing_reason || "—")}</td><td class="num">${esc(i.total_tokens)}</td>
    <td>${i.degraded ? pill("degraded", "warn") : pill("ok", "good")}</td></tr>`).join("");
  el.innerHTML = `
    <dl class="kv">
      <dt>model calls</dt><dd>${esc(m.model_calls)}</dd>
      <dt>tool calls</dt><dd>${esc(m.tool_calls)}</dd>
      <dt>specialists</dt><dd>${esc(m.specialists_invoked)}</dd>
      <dt>tokens</dt><dd>${esc(m.total_tokens)}</dd>
      <dt>duration</dt><dd>${esc(m.duration_ms)} ms</dd>
      <dt>inspections</dt><dd>${esc(m.security_inspections)}</dd>
    </dl>
    <div class="tablewrap" style="margin-top:8px"><table>
      <thead><tr><th>role</th><th>model</th><th>routing reason</th><th class="num">tokens</th><th>status</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="5" class="muted">no inferences</td></tr>`}</tbody>
    </table></div>`;
}

async function renderLearning() {
  const el = $("learning-body");
  const skills = await getJSON("/skills");
  const props = await getJSON("/runbook-proposals");
  const stub = (name, res) =>
    res.ok
      ? `${pill("available", "good")} ${name}: ${esc(JSON.stringify(res.body).slice(0, 80))}`
      : `${pill("pending", "warn")} <code>${name}</code> — route not implemented yet (HTTP ${res.status})`;
  el.innerHTML = `
    <div>${stub("/skills", skills)}</div>
    <div style="margin-top:6px">${stub("/runbook-proposals", props)}</div>
    <div class="note">Episode → candidate skill → HiddenLayer scan → replay → promote is owned by
    the recursive-learning track (built in parallel). These views light up when
    <code>/skills</code> and <code>/runbook-proposals</code> ship. The candidate-skill trigger and
    <code>known_false_leads</code> contract is in <code>fixtures/incidents/incident-one.json</code>.</div>`;
}

function renderSecurity(sec) {
  const el = $("security-body");
  if (!sec || sec.inspection_count === 0) {
    el.innerHTML = `<span class="muted">No security activity yet. Run the demo to exercise the poisoned-runbook quarantine and OpenShell denials.</span>`;
    return;
  }
  const events = sec.events || [];
  const denials = sec.denials || [];
  const notable = events.filter((e) => e.security_incident_created || e.action !== "allow");
  const evRows = notable.map((e) => `
    <tr><td class="mono">${esc(e.security_event_id)}</td><td>${esc(e.stage)}</td>
    <td>${esc(e.finding)}</td>
    <td>${pill(e.action, e.action === "allow" ? "good" : "bad")}</td>
    <td>${pill(e.agent_effect, e.agent_effect === "used_in_context" ? "good" : "bad")}</td>
    <td class="num">${e.trust_score_before ?? "—"}→${e.trust_score_after ?? "—"}</td></tr>`).join("");
  const dnRows = denials.map((d) => `
    <tr><td class="mono">${esc(d.denial_id)}</td><td>${esc(d.action)}</td>
    <td>${pill(d.effect, "bad")}</td><td>${esc(d.category)}</td><td>${esc(d.resource_category)}</td></tr>`).join("");
  el.innerHTML = `
    <div style="margin-bottom:8px">
      ${pill(sec.inspection_count + " inspections", "info")}
      ${pill(sec.quarantine_count + " quarantined", sec.quarantine_count ? "bad" : "good")}
      ${pill(sec.incident_count + " incidents", sec.incident_count ? "bad" : "good")}
      ${pill(sec.denial_count + " denials", sec.denial_count ? "bad" : "good")}
    </div>
    <div class="lbl muted">HiddenLayer findings (non-allow)</div>
    <div class="tablewrap"><table>
      <thead><tr><th>id</th><th>stage</th><th>finding</th><th>action</th><th>effect</th><th class="num">trust</th></tr></thead>
      <tbody>${evRows || `<tr><td colspan="6" class="muted">none</td></tr>`}</tbody></table></div>
    <div class="lbl muted" style="margin-top:10px">OpenShell denials (independent of HiddenLayer)</div>
    <div class="tablewrap"><table>
      <thead><tr><th>id</th><th>action</th><th>effect</th><th>category</th><th>resource</th></tr></thead>
      <tbody>${dnRows || `<tr><td colspan="5" class="muted">none</td></tr>`}</tbody></table></div>`;
}

function fmt(v) {
  if (v === true) return "yes";
  if (v === false) return "no";
  if (v === null || v === undefined) return "—";
  return v;
}
function renderComparison(cmp) {
  const el = $("comparison-body");
  if (!cmp) {
    el.innerHTML = `<span class="muted">Run the demo to compute the first-vs-learned comparison from persisted records.</span>`;
    return;
  }
  const ev = cmp.evaluation || {};
  const gate = ev.passed
    ? `<div class="gate pass">GATE PASS — ${esc(ev.improved_count)} metric(s) improved (≥ ${esc(ev.min_improved)} required)</div>`
    : `<div class="gate fail">GATE NOT MET — ${esc(ev.reason || "")}</div>`;
  const rows = (cmp.rows || []).map((r) => {
    let mark = `<span class="muted">=</span>`;
    if (r.improved === true) mark = pill("improved", "good");
    else if (r.improved === false && r.delta && r.delta !== 0) mark = pill("worse", "bad");
    return `<tr><td>${esc(r.label)}</td><td class="num">${fmt(r.first)}</td>
      <td class="num">${fmt(r.learned)}</td><td class="num">${fmt(r.delta)}</td><td>${mark}</td></tr>`;
  }).join("");
  el.innerHTML = `
    ${gate}
    <div class="tablewrap"><table>
      <thead><tr><th>metric</th><th class="num">first (incident 1)</th><th class="num">learned (incident 2)</th><th class="num">Δ</th><th>result</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <div class="note">Source: <code>${esc(cmp.source || "persisted_cycle_records")}</code>.
    In pure mock mode both incidents run the identical fixed pipeline, so improvement is expected to be 0
    until the skill-reuse path lands — the demo never fabricates a delta (see <code>fixtures/README.md</code>).</div>`;
}

// --- driver ---------------------------------------------------------------

async function refresh() {
  const conn = $("conn-badge");
  try {
    const st = await getJSON("/demo/state");
    conn.textContent = "API: ok"; conn.className = "badge badge-live";
    const b = st.body;
    renderMode(b.mode);
    $("empty-hint").style.display = b.has_run ? "none" : "block";
    renderAutonomy(b.incident_one);
    renderRevision(b.incident_one);
    renderEvidence(b.incident_one);
    renderRouting(b.incident_one);
    renderSecurity(b.security);
    renderComparison(b.comparison);
    await renderLearning();
  } catch (e) {
    conn.textContent = "API: unreachable"; conn.className = "badge badge-bad";
  }
}

async function runDemo() {
  const btn = $("run-btn");
  btn.disabled = true; btn.textContent = "Running…";
  try {
    await fetch(API + "/demo/run", { method: "POST" });
  } catch (e) { /* surfaced by refresh */ }
  btn.disabled = false; btn.textContent = "Run demo";
  await refresh();
}

$("run-btn").addEventListener("click", runDemo);
$("refresh-btn").addEventListener("click", refresh);
refresh();
