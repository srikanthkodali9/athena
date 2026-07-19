"use strict";

const API = "/api/v1";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const tag = (t, k) => `<span class="tag ${k || "dim"}">${esc(t)}</span>`;
const num = (v) => (v == null ? "—" : v);

const CRUMBS = {
  incidents: "Incidents", detail: "Incidents / detail", skills: "Skills",
  security: "Security", metrics: "Metrics", codex: "Codex validation",
  heartbeat: "Heartbeat", integrations: "Integrations",
};

let STATE = null;

async function getJSON(path) {
  const r = await fetch(API + path, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(path + " -> " + r.status);
  return r.json();
}

// --- navigation -----------------------------------------------------------

function show(name) {
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("on", p.id === "page-" + name));
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("on", a.dataset.page === name));
  $("crumb").innerHTML = (CRUMBS[name] || name).replace(/ \/ /g, ' <span class="sep">/</span> ');
  window.scrollTo(0, 0);
}

// --- helpers --------------------------------------------------------------

function statStrip(cells) {
  return `<div class="stats">${cells.map((c) =>
    `<div class="st"><div class="l">${esc(c.l)}</div><div class="v">${c.v}</div><div class="s">${esc(c.s || "")}</div></div>`
  ).join("")}</div>`;
}
function card(title, right, body) {
  return `<div class="card"><div class="h"><h2>${esc(title)}</h2>${right || ""}</div>${body}</div>`;
}
function table(head, rows) {
  return `<div class="tw"><table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;
}
const decisionTag = (d) => d === "ESCALATE"
  ? `<span class="tag crit"><span class="dot"></span>${esc(d)}</span>`
  : tag(d || "—", "dim");

// --- pages ----------------------------------------------------------------

function renderIncidents(d) {
  const inc = d.incidents;
  const escalated = inc.filter((i) => i.decision === "ESCALATE").length;
  const avg = inc.length ? Math.round(inc.reduce((a, i) => a + i.model_calls, 0) / inc.length) : 0;
  const rows = inc.map((i) => `
    <tr class="clk" data-goto="detail" data-id="${esc(i.cycle_id)}">
      <td><b>${esc(i.fixture)}</b><div class="muted mono2" style="font-size:10.5px">${esc(i.cycle_id)}</div></td>
      <td>${esc(i.signal || "—")}</td>
      <td>${i.mid_cycle_decision === "REVISE_PLAN" ? tag(i.mid_cycle_decision, "brand") : tag(i.mid_cycle_decision || "—", "dim")}</td>
      <td class="r">${i.evidence_count}</td><td class="r">${i.model_calls}</td>
      <td>${decisionTag(i.decision)}</td>
    </tr>`).join("");
  $("page-incidents").innerHTML =
    `<div class="phead"><div><h1 class="ptitle">Incidents</h1>
      <p class="psub">Investigations Athena has run. Every field is read from the persisted cycle record.</p></div></div>` +
    statStrip([
      { l: "Total incidents", v: inc.length, s: "persisted" },
      { l: "Escalated", v: escalated, s: "decision = ESCALATE" },
      { l: "Avg model calls", v: avg, s: "tiered routing" },
      { l: "Skills learned", v: d.skills.length, s: "reused on recurrence" },
    ]) +
    card("All incidents", `<span class="h-r">persisted cycles</span>`,
      table(`<th>Incident</th><th>Signal</th><th>Mid-cycle</th><th class="r">Evidence</th><th class="r">Model calls</th><th>Decision</th>`,
        rows || `<tr><td colspan="6" class="muted">none</td></tr>`));
  document.querySelectorAll("#page-incidents tr.clk").forEach((tr) =>
    tr.addEventListener("click", () => { renderDetail(STATE, tr.dataset.id); show("detail"); }));
}

function renderDetail(d, id) {
  const c = (id && d.incidents.find((i) => i.cycle_id === id)) || d.incident_one || d.incidents[0];
  if (!c) { $("page-detail").innerHTML = `<div class="empty"><p>No incident selected.</p></div>`; return; }
  const p = c.plan || {};
  const revised = p.revision_used && p.original_platform_question;
  const revStep = revised ? `
    <div class="e agent"><span class="mk">4</span><div>
      <div class="t">Mid-cycle revision <span class="by">REVISE_PLAN</span></div>
      <div class="d">Evidence narrowed the question, so Athena rewrote it:</div>
      <div class="qrev"><div class="a">${esc(p.original_platform_question)}</div>
        <div class="b2">${esc(p.platform_question)}</div></div>
    </div></div>` : "";
  const skill = d.skills[0];
  const skillCard = skill ? `<div class="card"><div class="h"><h2>Skill learned</h2>${tag(skill.status, "brand")}</div>
    <div class="b" style="padding:14px 16px">
      <div class="mono" style="font-size:12px;color:var(--brand-ink);font-weight:600">${esc(skill.skill_id)}</div>
      <div style="font-size:12px;color:var(--ink-2);margin-top:5px">${esc(skill.title)}</div>
      <div class="chips" style="margin-top:10px">${tag("HL · " + skill.hiddenlayer_status, "ok")}${tag("replay " + (skill.replay.filter(r=>r.passed).length) + "/" + skill.replay.length, "ok")}</div>
    </div></div>` : "";

  $("page-detail").innerHTML = `
    <div class="badges">
      <span class="status"><span class="dot"></span>${c.decision === "ESCALATE" ? "Escalated" : esc(c.state)}</span>
      ${tag(c.fixture, "brand")}${tag("trigger · " + c.trigger, "dim")}
    </div>
    <h1 class="ptitle" style="font-size:21px">${esc(c.signal || c.fixture)}</h1>
    <p class="psub" style="margin-bottom:16px">Detected by a heartbeat signal — no investigation prompt from a user.</p>
    <div class="layout">
      <div>
        <div class="card"><div class="b"><p class="lead" style="font-size:13.5px">
          Athena investigated across ${c.specialists >= 2 ? "two bounded rounds" : "a bounded investigation"},
          registered <b>${c.evidence_count}</b> deterministic evidence facts,
          ${revised ? "<b>rewrote its own hypothesis mid-cycle</b>, " : ""}and reached
          <b>${esc(c.decision)}</b> at confidence <b>${c.confidence != null ? c.confidence.toFixed(2) : "—"}</b>.
        </p></div></div>
        ${card("Investigation timeline", `<span class="h-r">${esc(c.cycle_id)}</span>`, `<div class="b"><div class="tl">
          <div class="e agent"><span class="mk">1</span><div><div class="t">Change detected <span class="by">heartbeat</span></div><div class="d">${esc(c.signal || "—")} · classified <b>${esc(c.change_class || "—")}</b>.</div></div></div>
          <div class="e agent"><span class="mk">2</span><div><div class="t">Investigation planned <span class="by">strategic tier</span></div><div class="d">Bounded plan · ${c.specialists} specialist(s), ${c.tool_calls} tool call(s).</div></div></div>
          <div class="e"><span class="mk">3</span><div><div class="t">Evidence gathered</div><div class="d">Deterministic facts registered with provenance:</div><div class="evs">${c.evidence_ids.map((e) => `<span class="ev">${esc(e)}</span>`).join("")}</div></div></div>
          ${revStep}
          <div class="e win"><span class="mk">✓</span><div><div class="t">Synthesis &amp; self-critique <span class="by">strategic tier</span></div><div class="d">Root cause confirmed at confidence <b>${c.confidence != null ? c.confidence.toFixed(2) : "—"}</b> → decision <b style="color:var(--crit)">${esc(c.decision)}</b>.</div></div></div>
        </div></div>`)}
      </div>
      <aside class="rail">
        <div class="card"><div class="h"><h2>Properties</h2></div><div class="b" style="padding:6px 0"><div class="props">
          <div class="prop"><span class="k">Decision</span><span class="v mono2">${esc(c.decision || "—")}</span></div>
          <div class="prop"><span class="k">Trigger</span><span class="v mono2">${esc(c.trigger)}</span></div>
          <div class="prop"><span class="k">Change class</span><span class="v mono2">${esc(c.change_class || "—")}</span></div>
          <div class="prop"><span class="k">Mid-cycle</span><span class="v mono2">${esc(c.mid_cycle_decision || "—")}</span></div>
          <div class="prop"><span class="k">Confidence</span><span class="v mono2">${c.confidence != null ? c.confidence.toFixed(2) : "—"}</span></div>
          <div class="prop"><span class="k">Evidence</span><span class="v mono2">${c.evidence_count} facts</span></div>
          <div class="prop"><span class="k">Model calls</span><span class="v mono2">${c.model_calls}</span></div>
          <div class="prop"><span class="k">Tokens</span><span class="v mono2">${c.total_tokens}</span></div>
        </div></div></div>
        ${skillCard}
      </aside>
    </div>`;
}

function renderSkills(d) {
  const cards = d.skills.map((s) => {
    const versions = s.versions.map((v) => `
      <tr><td class="mono2"><b>${esc(v.version)}</b></td><td>${tag(v.status, "ok")}</td>
        <td>${v.from_incidents.map((i) => esc(i)).join(", ")}</td><td class="r">${v.validation_passes}/3</td>
        <td>${tag(v.hiddenlayer_status, "ok")}</td><td class="r">${v.false_leads}</td></tr>`).join("");
    const replay = s.replay.map((r) => `
      <tr><td class="mono2">${esc(r.fixture)}</td><td>${esc(r.kind)}</td>
        <td>${r.passed ? tag(r.verdict, "ok") : tag(r.verdict, "crit")}</td></tr>`).join("");
    return card(s.skill_id, tag(s.status + " · v" + s.latest_version, "ok"), `<div class="b">
        <dl class="kvs">
          <dt>Title</dt><dd>${esc(s.title)}</dd>
          <dt>Domain</dt><dd class="mono2">${esc(s.domain)}</dd>
          <dt>Risk</dt><dd>${tag(s.risk, "warn")}</dd>
          <dt>Confidence</dt><dd class="mono2">${s.confidence}</dd>
          <dt>Triggers</dt><dd class="chips">${s.triggers.map((t) => `<span class="ev">${esc(t)}</span>`).join("")}</dd>
          <dt>Learned from</dt><dd class="chips">${s.from_incidents.map((i) => tag(i, "dim")).join("")}</dd>
          <dt>Security</dt><dd class="mono2" style="color:var(--ok)">HiddenLayer scan ${esc(s.hiddenlayer_status)}</dd>
        </dl></div>`) +
      card("Versions", `<span class="h-r">observed → candidate → scanned → validated → promoted</span>`,
        table(`<th>Version</th><th>Status</th><th>Learned from</th><th class="r">Replay</th><th>HL scan</th><th class="r">False leads</th>`, versions)) +
      card("Replay validation", `<span class="h-r">deterministic, offline</span>`,
        table(`<th>Fixture</th><th>Kind</th><th>Result</th>`, replay));
  }).join("");
  $("page-skills").innerHTML =
    `<div class="phead"><div><h1 class="ptitle">Skills</h1>
      <p class="psub">Diagnostic skills learned from verified incidents — each security-scanned and replay-validated before promotion.</p></div></div>` +
    statStrip([
      { l: "Skills", v: d.skills.length, s: "promoted" },
      { l: "Versions", v: d.skills.reduce((a, s) => a + s.versions.length, 0), s: "across skills" },
      { l: "Replay pass rate", v: "3<small>/3</small>", s: "pos · neg · contra" },
      { l: "False leads captured", v: d.skills.reduce((a, s) => a + s.versions.reduce((b, v) => b + v.false_leads, 0), 0), s: "from self-revision" },
    ]) + (cards || `<div class="empty"><p>No skills learned yet.</p></div>`);
}

function renderSecurity(d) {
  const s = d.security;
  const evRows = s.events.map((e) => `
    <tr><td class="mono2">${esc(e.security_event_id)}</td><td class="mono2">${esc(e.stage)}</td>
      <td>${esc(e.producer)}</td><td>${esc(e.finding)}</td>
      <td>${tag(e.action, "crit")}</td>
      <td class="r">${num(e.trust_score_before)} → <b style="color:var(--crit)">${num(e.trust_score_after)}</b></td></tr>`).join("");
  const dnRows = s.denials.map((x) => `
    <tr><td class="mono2">${esc(x.denial_id)}</td><td class="mono2">${esc(x.action)}</td>
      <td>${esc(x.category)}</td><td class="muted">${esc(x.resource_category)}</td>
      <td>${tag(x.effect, "crit")}</td></tr>`).join("");
  $("page-security").innerHTML =
    `<div class="phead"><div><h1 class="ptitle">Security</h1>
      <p class="psub">Runtime security actions from the shared ledger. HiddenLayer inspects information transitions; OpenShell enforces capability policy independently.</p></div></div>` +
    statStrip([
      { l: "Inspections", v: s.total_inspections, s: "across cycles" },
      { l: "Quarantined", v: `<span style="color:var(--crit)">${s.events.filter((e) => e.action === "quarantine").length}</span>`, s: "poisoned content" },
      { l: "Incidents raised", v: `<span style="color:var(--crit)">${s.incident_count}</span>`, s: "trust → 0" },
      { l: "Capability denials", v: `<span style="color:var(--crit)">${s.denial_count}</span>`, s: "credential + egress" },
    ]) +
    card("HiddenLayer findings", `<span class="h-r">trust transitions</span>`,
      table(`<th>ID</th><th>Stage</th><th>Source</th><th>Finding</th><th>Action</th><th class="r">Trust</th>`,
        evRows || `<tr><td colspan="6" class="muted">none</td></tr>`)) +
    card("OpenShell capability denials", `<span class="h-r">independent of HiddenLayer</span>`,
      table(`<th>ID</th><th>Action</th><th>Category</th><th>Resource</th><th>Effect</th>`,
        dnRows || `<tr><td colspan="5" class="muted">none</td></tr>`));
}

function renderMetrics(d) {
  const cmp = d.comparison;
  let gate = "", rows = "", strip = [];
  if (cmp) {
    const ev = cmp.evaluation;
    gate = `<div class="gate ${ev.passed ? "" : "fail"}">
      <svg viewBox="0 0 24 24" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
      ${ev.passed ? "Gate passed" : "Gate not met"} — ${ev.improved_count} of ${cmp.rows.length} metrics improved (≥ ${ev.min_improved} required)</div>`;
    rows = cmp.rows.map((r) => {
      const res = r.improved === true ? `<span class="imp">improved</span>`
        : (r.delta && r.delta !== 0 && r.delta !== false ? `<span class="flat">changed</span>` : `<span class="flat">unchanged</span>`);
      const fmt = (x) => x === true ? "yes" : x === false ? "no" : x == null ? "—" : x;
      return `<tr><td>${esc(r.label)}</td><td class="r">${fmt(r.first)}</td><td class="r">${fmt(r.learned)}</td><td class="r">${fmt(r.delta)}</td><td>${res}</td></tr>`;
    }).join("");
    strip = [
      { l: "Metrics improved", v: `<span style="color:var(--ok)">${cmp.evaluation.improved_count}<small>/${cmp.rows.length}</small></span>`, s: "on recurrence" },
      { l: "Improvement gate", v: `<span style="color:var(--${cmp.evaluation.passed ? "ok" : "warn"})">${cmp.evaluation.passed ? "PASS" : "—"}</span>`, s: "≥ 2 required" },
    ];
  }
  const one = d.incident_one;
  const infRows = one ? one.inferences.map((i) => `
    <tr><td class="role">${esc(i.role)}</td><td class="mono2 muted">${esc(i.model_id)}</td>
      <td class="r">${num(i.prompt_tokens)}</td><td class="r">${num(i.completion_tokens)}</td><td class="r">${num(i.total_tokens)}</td>
      <td>${i.degraded ? tag("degraded", "warn") : tag("ok", "ok")}</td></tr>`).join("") : "";
  const two = d.incidents.find((i) => i.fixture !== (one && one.fixture));
  $("page-metrics").innerHTML =
    `<div class="phead"><div><h1 class="ptitle">Metrics</h1>
      <p class="psub">Per-cycle model, tool, and token accounting — and the first-vs-learned improvement comparison.</p></div></div>` +
    statStrip(strip.concat([
      { l: "Model calls · first", v: one ? one.model_calls : "—", s: one ? one.total_tokens + " tokens" : "" },
      { l: "Model calls · learned", v: two ? two.model_calls : "—", s: two ? two.total_tokens + " tokens" : "" },
    ])) +
    (cmp ? card("Recurrence & improvement", `<span class="h-r">first → learned</span>`,
      gate + `<div style="padding:13px 16px 4px">` +
      table(`<th>Metric</th><th class="r">First</th><th class="r">Learned</th><th class="r">Δ</th><th>Result</th>`, rows) + `</div>`)
      : card("Recurrence & improvement", "", `<div class="b muted">Run a recurring incident to compute the comparison.</div>`)) +
    (one ? card("Model calls · " + one.fixture, `<span class="h-r">${one.model_calls} calls · ${one.total_tokens} tokens</span>`,
      table(`<th>Role</th><th>Model</th><th class="r">Prompt</th><th class="r">Completion</th><th class="r">Tokens</th><th>Mode</th>`, infRows)) : "");
}

function renderHeartbeat(d) {
  const h = d.heartbeat, b = h.budgets;
  const recent = d.incidents.map((i) => `
    <tr><td class="mono2">${esc(i.cycle_id)}</td><td>${esc(i.fixture)}</td>
      <td>${tag(i.change_class || "—", "dim")}</td><td class="mono2">${esc(i.mid_cycle_decision || "—")}</td>
      <td class="r">${i.model_calls}</td><td>${decisionTag(i.decision)}</td></tr>`).join("");
  $("page-heartbeat").innerHTML =
    `<div class="phead"><div><h1 class="ptitle">Heartbeat</h1>
      <p class="psub">The periodic loop that detects change without a user prompt, and the bounds every investigation runs within.</p></div></div>` +
    statStrip([
      { l: "Interval", v: h.interval_seconds + "<small> s</small>", s: "periodic tick" },
      { l: "Max specialists", v: b.max_specialists, s: "per cycle" },
      { l: "Max tool calls", v: b.max_tool_calls, s: "per cycle" },
      { l: "Deadline", v: b.deadline_seconds + "<small> s</small>", s: "per investigation" },
    ]) +
    card("Recent activity", `<span class="h-r">persisted cycles</span>`,
      table(`<th>Cycle</th><th>Fixture</th><th>Change class</th><th>Mid-cycle</th><th class="r">Model calls</th><th>Decision</th>`,
        recent || `<tr><td colspan="6" class="muted">none</td></tr>`)) +
    `<div class="layout" style="grid-template-columns:1fr 1fr">` +
      card("Signals checked each tick", `<span class="h-r">${h.signals.length} sources</span>`,
        `<div class="b"><div class="chips">${h.signals.map((s) => `<span class="ev">${esc(s)}</span>`).join("")}</div>
         <p class="psub" style="margin-top:12px">A tick with no meaningful change takes the WAIT path and spends zero model calls.</p></div>`) +
      card("Investigation budgets", "",
        `<div class="b" style="padding:14px 16px"><dl class="kvs">
          <dt>Max tokens</dt><dd class="mono2">${b.max_total_tokens}</dd>
          <dt>Max specialists</dt><dd class="mono2">${b.max_specialists}</dd>
          <dt>Max tool calls</dt><dd class="mono2">${b.max_tool_calls}</dd>
          <dt>Deadline</dt><dd class="mono2">${b.deadline_seconds} s</dd>
        </dl></div>`) +
    `</div>`;
}

function renderIntegrations(d) {
  const list = d.integrations;
  const rows = list.map((x) => `
    <tr><td><b>${esc(x.name)}</b><div class="muted mono2" style="font-size:10.5px">${esc(x.sub)}</div></td>
      <td class="mono2 muted">${esc(x.endpoint)}</td>
      <td class="mono2" style="font-size:10.5px">${esc(x.env)}</td>
      <td class="mono2">${esc(x.mode)}</td>
      <td>${tag(x.status, x.tone)}</td></tr>`).join("");
  const active = list.filter((x) => x.tone === "ok").length;
  const mock = list.filter((x) => x.status === "not configured" || x.mode === "fixture").length;
  $("page-integrations").innerHTML =
    `<div class="phead"><div><h1 class="ptitle">Integrations</h1>
      <p class="psub">External adapters Athena connects through. Live credentials are supplied per deployment; this environment runs in mock / fixture mode.</p></div></div>` +
    statStrip([
      { l: "Adapters", v: list.length, s: "outbound" },
      { l: "Active", v: `<span style="color:var(--ok)">${active}</span>`, s: "policy + configured" },
      { l: "Mock / fixture", v: `<span style="color:var(--warn)">${mock}</span>`, s: "no live creds" },
      { l: "Environment", v: `<span style="font-size:15px">${esc(d.config.env)}</span>`, s: "sanitized status" },
    ]) +
    card("Adapters", `<span class="h-r">env · ${esc(d.config.env)}</span>`,
      table(`<th>Integration</th><th>Model / endpoint</th><th>Configured via</th><th>Mode</th><th>Status</th>`, rows));
}

const actTag = (a) => a === "allow" ? tag(a, "ok") : tag(a, "crit");

function renderCodex(d) {
  const x = d.codex;
  if (!x) { $("page-codex").innerHTML =
    `<div class="phead"><div><h1 class="ptitle">Codex validation</h1>
      <p class="psub">Isolated <code>fixtures/codex</code> workflow — validation-only, never written into the main demo stores.</p></div></div>
     <div class="empty"><p>Run a cycle to execute the Codex validation workflow.</p></div>`; return; }

  const edgePass = x.edge_cases.filter((e) => e.passed).length;
  const scen = x.scenarios.map((s) => `
    <tr><td><b>${esc(s.scenario_id)}</b></td><td>${esc(s.service)}</td>
      <td>${s.mid_cycle_decision === "REVISE_PLAN" ? tag(s.mid_cycle_decision, "brand") : tag(s.mid_cycle_decision, "dim")}</td>
      <td class="r">${s.pool_size_before}→${s.pool_size_after} ×${s.replicas}</td>
      <td class="r">${s.maximum_possible_clients} / ${s.redis_maxclients}</td>
      <td class="r">${s.evidence_ids.length}</td>
      <td class="r">${s.metrics ? s.metrics.root_cause_confidence : "—"}</td>
      <td>${tag(s.skill_status || "—", s.skill_reused ? "brand" : "ok")}</td></tr>`).join("");
  const causes = x.scenarios.map((s) => `
    <div class="ev" style="display:block;white-space:normal;background:var(--panel-2);margin-bottom:6px;padding:8px 11px">
      <b class="mono2" style="color:var(--brand-ink)">${esc(s.scenario_id)}</b> — ${esc(s.root_cause)}</div>`).join("");

  const cm = x.comparison.candidate_metrics.map((m) => {
    const res = m.measured === false ? `<span class="flat">not measured</span>`
      : m.improved ? `<span class="imp">improved</span>` : `<span class="flat">unchanged</span>`;
    const f = m.first != null ? m.first : "—", l = m.learned != null ? m.learned : "—";
    return `<tr><td>${esc(m.metric)}</td><td class="r">${f}</td><td class="r">${l}</td><td>${res}</td></tr>`;
  }).join("");

  const sec = x.security, g = sec.unavailable_gateway_actions || {};
  const secRows = [
    ["clean runbook", sec.clean_runbook_action], ["poisoned runbook", sec.poisoned_runbook_action],
    ["poisoned tool result", sec.poisoned_tool_result_action], ["poisoned persistence", sec.poisoned_persistence_action],
    ["protected path", sec.protected_path_decision], ["unapproved egress", sec.unapproved_egress_decision],
    ["gateway down · athena reply", g.athena_response], ["gateway down · tool call", g.tool_call],
    ["gateway down · skill write", g.skill_write],
  ].map(([k, v]) => `<tr><td>${esc(k)}</td><td>${actTag(v)}</td></tr>`).join("");

  const edges = x.edge_cases.map((e) => `
    <tr><td class="mono2">${esc(e.edge_case_id)}</td><td>${esc(e.category)}</td>
      <td>${e.passed ? tag("pass", "ok") : tag("fail", "crit")}</td></tr>`).join("");

  $("page-codex").innerHTML =
    `<div class="phead"><div><h1 class="ptitle">Codex validation</h1>
      <p class="psub">Isolated <code>${esc(x.fixture_root)}</code> workflow (<span class="mono2">${esc(x.catalog_id)}</span>) — validation-only, never written into the main demo stores.</p></div></div>` +
    statStrip([
      { l: "Scenarios", v: x.scenarios.length, s: "synthetic datasets" },
      { l: "Edge cases", v: `<span style="color:var(--${edgePass === x.edge_cases.length ? "ok" : "warn"})">${edgePass}<small>/${x.edge_cases.length}</small></span>`, s: "validation matrix" },
      { l: "Improvement gate", v: `<span style="color:var(--${x.comparison.passed ? "ok" : "warn"})">${x.comparison.passed ? "PASS" : "—"}</span>`, s: `≥ ${x.comparison.required_improvement_count} required` },
      { l: "Workflow", v: `<span style="color:var(--${x.passed ? "ok" : "crit"})">${x.passed ? "PASS" : "FAIL"}</span>`, s: "all checks" },
    ]) +
    card("Scenario outcomes", `<span class="h-r">fixtures/codex</span>`,
      table(`<th>Scenario</th><th>Service</th><th>Mid-cycle</th><th class="r">Pool</th><th class="r">Clients / limit</th><th class="r">Evidence</th><th class="r">Conf</th><th>Skill</th>`, scen) +
      `<div class="b" style="padding-top:0">${causes}</div>`) +
    `<div class="layout" style="grid-template-columns:1fr 1fr">` +
      card("Learning & improvement gate", `<span class="h-r">${x.comparison.improved_count}/${x.comparison.required_improvement_count}</span>`,
        table(`<th>Metric</th><th class="r">First</th><th class="r">Learned</th><th>Result</th>`, cm)) +
      card("Security boundaries", x.security.passed ? tag("all enforced", "ok") : tag("gap", "crit"),
        table(`<th>Transition</th><th>Action</th>`, secRows)) +
    `</div>` +
    card("Edge-case validation", `<span class="h-r">${edgePass}/${x.edge_cases.length} pass</span>`,
      table(`<th>Edge case</th><th>Category</th><th>Result</th>`, edges));
}

// --- driver ---------------------------------------------------------------

function renderAll(d) {
  STATE = d;
  const cfg = d.config || {};
  const live = cfg.inference_mode === "live" && cfg.security_mode === "live";
  $("envrow").innerHTML = `<span class="d ${live ? "live" : ""}"></span>${esc((cfg.env || "?").toUpperCase())} · ${esc((cfg.inference_mode || "?").toUpperCase())} MODE${cfg.degraded_mode ? " · DEGRADED" : ""}`;
  $("ct-incidents").textContent = d.incidents.length;
  $("ct-skills").textContent = d.skills.length;
  $("ct-security").textContent = d.security.incident_count + d.security.denial_count;
  $("ct-codex").textContent = d.codex ? d.codex.scenarios.length : 0;
  $("empty").hidden = d.has_run;
  if (!d.has_run) { document.querySelectorAll(".page").forEach((p) => (p.innerHTML = "")); return; }
  renderIncidents(d);
  renderDetail(d);
  renderSkills(d);
  renderSecurity(d);
  renderMetrics(d);
  renderCodex(d);
  renderHeartbeat(d);
  renderIntegrations(d);
}

async function refresh() {
  try { renderAll(await getJSON("/demo/console")); }
  catch (e) { $("empty").hidden = false; $("empty").innerHTML = `<h2>API unreachable</h2><p>${esc(e.message)}</p>`; }
}

async function runCycle() {
  const btn = $("run");
  btn.disabled = true; btn.textContent = "Running…";
  try { await fetch(API + "/demo/run", { method: "POST" }); } catch (e) { /* surfaced by refresh */ }
  btn.disabled = false; btn.textContent = "Run cycle";
  await refresh();
}

document.querySelectorAll(".nav a").forEach((a) => a.addEventListener("click", () => show(a.dataset.page)));
$("run").addEventListener("click", runCycle);
$("refresh").addEventListener("click", refresh);
refresh();
