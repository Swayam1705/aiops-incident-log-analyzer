// UI helper layer. Same-origin fetch only; the UI never executes commands.
// No inline handlers are used so the strict Content-Security-Policy holds.
"use strict";

const $ = (sel, root) => (root || document).querySelector(sel);

async function api(path, opts) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { detail: text }; }
  if (!res.ok) {
    const err = new Error((data && data.detail) || ("HTTP " + res.status));
    err.status = res.status; err.data = data;
    throw err;
  }
  return data;
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function sevBadge(sev) { return `<span class="sev ${sev || "info"}">${(sev || "info")}</span>`; }
function fmtTime(ts) { if (!ts) return "-"; try { return new Date(ts).toLocaleString(); } catch (e) { return ts; } }

// ---- shared liveness poll ----
async function pollLive() {
  const live = $("#live-indicator");
  if (!live) return;
  async function tick() {
    try {
      const r = await fetch("/health/live");
      live.className = "pill";
      live.innerHTML = `<span class="dot ${r.ok ? "on" : "off"}"></span> ${r.ok ? "service healthy" : "unhealthy"}`;
    } catch (e) {
      live.className = "pill";
      live.innerHTML = `<span class="dot off"></span> unreachable`;
    }
  }
  await tick();
  setInterval(tick, 5000);
}

// ---- Overview ----
async function loadOverview() {
  try {
    const h = await api("/health");
    const c = h.components || {};
    const si = $("#stat-incidents"); if (si) si.textContent = c.incident_count != null ? c.incident_count : "-";
    const sl = $("#stat-llm"); if (sl) sl.innerHTML = c.ollama && c.ollama.available
      ? `<span class="dot on"></span> ${escapeHtml(c.ollama.model)}` : `<span class="dot off"></span> unavailable`;
    const sn = $("#stat-notif"); if (sn) sn.innerHTML = c.notification && c.notification.configured
      ? `<span class="dot on"></span> configured` : `<span class="dot off"></span> not set`;
    const ss = $("#stat-sim"); if (ss) ss.innerHTML = c.simulator && c.simulator.running
      ? `<span class="dot on"></span> running` : `<span class="dot off"></span> stopped`;
  } catch (e) { /* liveness poll already surfaces unreachability */ }
  try {
    const inc = await api("/incidents");
    renderIncidentTable($("#recent-incidents"), inc.incidents || [], 6);
  } catch (e) {
    const st = $("#recent-incidents-state");
    if (st) { st.className = "state error"; st.textContent = "Could not load incidents: " + e.message; }
  }
  const fb = $("#fault-btn"); if (fb) fb.addEventListener("click", injectFault);
  const cb = $("#fault-clear"); if (cb) cb.addEventListener("click", clearFault);
  const ab = $("#analyze-btn"); if (ab) ab.addEventListener("click", runAnalyze);
}

function renderIncidentTable(container, incidents, limit) {
  if (!container) return;
  const rows = (incidents || []).slice(0, limit || (incidents || []).length);
  if (!rows.length) {
    container.innerHTML = `<div class="state">No incidents yet. Inject a failure, then run the analyzer.</div>`;
    return;
  }
  let html = `<table><thead><tr><th>Incident</th><th>Severity</th><th>Suspected cause</th><th>Source</th><th>Validation</th><th>Time</th></tr></thead><tbody>`;
  rows.forEach(r => {
    html += `<tr>
      <td><a href="/ui/incidents/${encodeURIComponent(r.incident_id)}">${escapeHtml(r.incident_id)}</a></td>
      <td>${sevBadge(r.severity)}</td>
      <td class="mono">${escapeHtml(r.suspected_root_cause || "unknown")}</td>
      <td><span class="badge ${r.analysis_source === "llm-assisted" ? "llm" : "rule"}">${escapeHtml(r.analysis_source)}</span></td>
      <td><span class="badge ${r.validation || "pending"}">${escapeHtml(r.validation || "pending")}</span></td>
      <td class="muted">${fmtTime(r.timestamp)}</td>
    </tr>`;
  });
  container.innerHTML = html + `</tbody></table>`;
}

async function injectFault() {
  const sel = $("#fault-select"), out = $("#fault-output"), btn = $("#fault-btn");
  if (!sel) return;
  btn.disabled = true; out.className = "alert";
  out.innerHTML = `<span class="spinner"></span> injecting ${escapeHtml(sel.value)} ...`;
  try {
    const res = await api("/admin/faults", { method: "POST", body: JSON.stringify({ scenario: sel.value }) });
    out.className = "alert ok";
    out.innerHTML = `Injected <b>${escapeHtml(res.fault.scenario)}</b> (ground truth <code>${escapeHtml(res.ground_truth)}</code>) for ${res.fault.remaining_seconds}s.`;
  } catch (e) { out.className = "alert error"; out.textContent = "Injection failed: " + e.message; }
  finally { btn.disabled = false; }
}

async function clearFault() {
  const out = $("#fault-output");
  try { await api("/admin/faults/clear", { method: "POST" }); out.className = "alert ok"; out.textContent = "Active fault cleared."; }
  catch (e) { out.className = "alert error"; out.textContent = "Clear failed: " + e.message; }
}

async function runAnalyze() {
  const out = $("#analyze-output"), btn = $("#analyze-btn");
  btn.disabled = true; out.className = "alert";
  out.innerHTML = `<span class="spinner"></span> analyzing latest log window ...`;
  try {
    const res = await api("/analyze", { method: "POST", body: JSON.stringify({}) });
    if (res.incident_created && res.incident) {
      out.className = "alert ok";
      out.innerHTML = `Incident <a href="/ui/incidents/${encodeURIComponent(res.incident.incident_id)}">${escapeHtml(res.incident.incident_id)}</a> created — ${sevBadge(res.incident.severity)} <code>${escapeHtml(res.incident.suspected_root_cause || "unknown")}</code> (${escapeHtml(res.analysis_source)}).`;
      loadOverview();
    } else {
      out.className = "alert warn";
      out.textContent = `No incident created (${res.error_lines} error line(s), ${res.noise_lines} noise ignored). Normal traffic → no critical incident.`;
    }
  } catch (e) { out.className = "alert error"; out.textContent = "Analysis failed: " + e.message; }
  finally { btn.disabled = false; }
}

// ---- Incidents list ----
async function loadIncidentsList() {
  try {
    const inc = await api("/incidents");
    renderIncidentTable($("#incidents-table"), inc.incidents || [], 1000);
  } catch (e) {
    const st = $("#incidents-state");
    if (st) { st.className = "state error"; st.textContent = "Could not load incidents: " + e.message; }
  }
}

// ---- Incident detail ----
async function loadIncidentDetail(id) {
  const box = $("#incident-detail");
  if (!box) return;
  try {
    const d = await api("/incidents/" + encodeURIComponent(id));
    const ai = d.ai_analysis || {}, rule = d.rule_analysis || {}, val = d.validation || {};
    const evidence = (d.evidence_lines || []).map(l => `<div>${escapeHtml(l)}</div>`).join("") || `<div class="muted">no evidence lines</div>`;
    box.innerHTML = `
      <div class="row" style="justify-content:space-between;align-items:flex-start">
        <div><h1>Incident ${escapeHtml(d.incident_id)}</h1><p class="sub">${escapeHtml(d.summary || "")}</p></div>
        <div>${sevBadge(d.severity)}</div>
      </div>
      <div class="grid cols-2">
        <div class="card">
          <h3>Rule-based analysis</h3>
          <dl class="kv">
            <dt>Suspected cause</dt><dd class="mono">${escapeHtml(d.suspected_root_cause || "unknown")}</dd>
            <dt>Confidence</dt><dd>${(d.confidence != null ? d.confidence.toFixed(2) : "-")}</dd>
            <dt>Error lines</dt><dd>${rule.error_lines != null ? rule.error_lines : "-"}</dd>
            <dt>Noise ignored</dt><dd>${rule.noise_lines != null ? rule.noise_lines : "-"}</dd>
            <dt>Burst</dt><dd>${rule.burst_detected ? ("yes (size " + rule.burst_size + ")") : "no"}</dd>
            <dt>Ground truth</dt><dd class="mono">${escapeHtml(d.ground_truth || "n/a")}</dd>
          </dl>
          ${(rule.reasons || []).length ? `<p class="notice">${rule.reasons.map(escapeHtml).join("<br>")}</p>` : ""}
        </div>
        <div class="card">
          <h3>AI-assisted analysis <span class="badge">advisory</span></h3>
          ${ai.available ? `
            <dl class="kv">
              <dt>Status</dt><dd>${escapeHtml(ai.status)}</dd>
              <dt>Model</dt><dd class="mono">${escapeHtml(ai.model || "-")}</dd>
              <dt>Suspected cause</dt><dd class="mono">${escapeHtml(ai.suspected_root_cause || "unknown")}</dd>
              <dt>Confidence</dt><dd>${ai.confidence != null ? ai.confidence.toFixed(2) : "-"}</dd>
              <dt>Agreed with rules</dt><dd>${ai.agreed_with_rules === null ? "n/a" : (ai.agreed_with_rules ? "yes" : "no")}</dd>
            </dl>
            <p class="muted">${escapeHtml(ai.summary || "")}</p>
            ${ai.recommended_action ? `<p class="notice">${escapeHtml(ai.recommended_action)}</p>` : ""}
          ` : `
            <div class="alert warn">AI analysis unavailable (<code>${escapeHtml(ai.status || "n/a")}</code>). The rule-based report stands on its own.</div>
            ${ai.error ? `<p class="muted mono">${escapeHtml(ai.error)}</p>` : ""}
          `}
        </div>
      </div>
      <h2>Log evidence</h2>
      <div class="evidence">${evidence}</div>
      <h2>Recommended action <span class="badge">advisory — requires human validation</span></h2>
      <div class="card"><p>${escapeHtml(d.recommended_action || "")}</p>
      ${d.runbook_id ? `<p class="muted">Runbook: <code>${escapeHtml(d.runbook_id)}</code> — no action is executed automatically.</p>` : ""}</div>
      <h2>Human validation</h2>
      <div class="card">
        <p>Current status: <span class="badge ${val.status || "pending"}">${escapeHtml(val.status || "pending")}</span>
        ${val.validated_by ? ` by ${escapeHtml(val.validated_by)}` : ""} ${val.validated_at ? fmtTime(val.validated_at) : ""}</p>
        <div class="row">
          <button data-validate="confirmed">Confirm diagnosis</button>
          <button class="ghost" data-validate="false_positive">Mark false positive</button>
          <button class="ghost" data-validate="rejected">Reject</button>
        </div>
        <div id="validate-output" class="mt"></div>
      </div>`;
    box.querySelectorAll("[data-validate]").forEach(btn => {
      btn.addEventListener("click", () => validateIncident(id, btn.getAttribute("data-validate")));
    });
  } catch (e) {
    box.innerHTML = `<div class="state error">Could not load incident: ${escapeHtml(e.message)}</div>`;
  }
}

async function validateIncident(id, status) {
  const out = $("#validate-output");
  try {
    await api("/incidents/" + encodeURIComponent(id) + "/validate", { method: "POST", body: JSON.stringify({ status, validated_by: "ui-user" }) });
    if (out) { out.className = "alert ok"; out.textContent = "Validation recorded: " + status; }
    loadIncidentDetail(id);
  } catch (e) { if (out) { out.className = "alert error"; out.textContent = "Validation failed: " + e.message; } }
}

// ---- Evaluation ----
async function runEvaluation() {
  const out = $("#eval-output"), btn = $("#eval-btn");
  btn.disabled = true; out.className = "state";
  out.innerHTML = `<span class="spinner"></span> running evaluation across all scenarios ...`;
  try { const r = await api("/evaluation/run?window_size=200", { method: "POST" }); renderEvaluation(r); }
  catch (e) { out.className = "state error"; out.textContent = "Evaluation failed: " + e.message; }
  finally { btn.disabled = false; }
}

function renderEvaluation(r) {
  const out = $("#eval-output");
  out.className = "";
  let html = `<div class="grid cols-3">
    <div class="card stat"><span class="label">Rule-based accuracy</span><span class="value">${escapeHtml(r.rule_accuracy)}</span></div>
    <div class="card stat"><span class="label">LLM accuracy</span><span class="value">${escapeHtml(r.llm_accuracy)}</span></div>
    <div class="card stat"><span class="label">False positives</span><span class="value">${r.false_positives}</span></div>
  </div>`;
  html += `<h2>Results</h2><table><thead><tr><th>Scenario</th><th>Expected</th><th>Rule detected</th><th>Conf.</th><th>LLM status</th><th>LLM detected</th><th>Outcome</th></tr></thead><tbody>`;
  (r.scenarios || []).forEach(s => {
    const ok = (s.outcome === "match" || s.outcome === "true_negative");
    const bad = (s.outcome === "false_positive" || s.outcome === "mismatch");
    const oc = ok ? "low" : (bad ? "critical" : "medium");
    html += `<tr>
      <td>${escapeHtml(s.label)}</td><td class="mono">${escapeHtml(s.expected_cause || "-")}</td>
      <td class="mono">${escapeHtml(s.rule_detected || "unknown")}</td><td>${s.rule_confidence.toFixed(2)}</td>
      <td><span class="badge">${escapeHtml(s.llm_status)}</span></td><td class="mono">${escapeHtml(s.llm_detected || "-")}</td>
      <td><span class="sev ${oc}">${escapeHtml(s.outcome)}</span></td></tr>`;
  });
  html += `</tbody></table>
  <p class="notice">Ground truth is fixed by the specification and never altered to match analyzer output. LLM accuracy is reported separately and may abstain (unknown / insufficient evidence).</p>`;
  out.innerHTML = html;
}

// ---- Health ----
async function loadHealth() {
  const box = $("#health-detail");
  if (!box) return;
  try { const h = await api("/health"); box.innerHTML = `<pre>${escapeHtml(JSON.stringify(h, null, 2))}</pre>`; }
  catch (e) { box.innerHTML = `<div class="state error">Could not load health: ${escapeHtml(e.message)}</div>`; }
  const rb = $("#health-refresh"); if (rb) rb.addEventListener("click", loadHealth);
}

// ---- init dispatcher ----
document.addEventListener("DOMContentLoaded", () => {
  pollLive();
  const page = document.body.getAttribute("data-page");
  if (page === "overview") loadOverview();
  else if (page === "incidents") {
    const id = document.body.getAttribute("data-incident-id");
    if (id) loadIncidentDetail(id); else loadIncidentsList();
  }
  else if (page === "evaluation") { const b = $("#eval-btn"); if (b) b.addEventListener("click", runEvaluation); }
  else if (page === "health") loadHealth();
});
