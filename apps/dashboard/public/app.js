// Plant health dashboard. Talks only to this Worker's /api/*; no tokens here.
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const STATUS = {
  healthy: { label: "Healthy", icon: "✓" },
  warning: { label: "Warning", icon: "!" },
  critical: { label: "Critical", icon: "▲" },
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtTime = (s) => { if (!s) return "—"; const d = new Date(s + "Z"); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${String(d.getUTCHours()).padStart(2, "0")}:00`; };
const fmtDate = (s) => { if (!s) return "—"; const d = new Date(s + "Z"); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; };
const pct = (p) => (p == null ? "—" : `${Math.round(p * 100)}%`);
const statusHtml = (s) => `<span class="status ${s}"><span class="dot" aria-hidden="true"></span><span class="icon" aria-hidden="true">${STATUS[s].icon}</span>${STATUS[s].label}</span>`;

let state = { overview: null, filter: "", openAsset: null };

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "content-type": "application/json", ...(opts.headers || {}) } });
  let data = null;
  try { data = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new Error(data?.detail || `HTTP ${r.status}`);
  return data;
}

function notice(msg) {
  const n = $("#notice");
  n.hidden = !msg;
  n.textContent = msg || "";
}

// ---------------------------------------------------------------- overview

async function loadOverview() {
  try {
    state.overview = await api("/api/overview");
    notice("");
    render();
  } catch (e) {
    notice(`Could not load the plant: ${e.message}`);
  }
}

function render() {
  const o = state.overview;
  $("#sim-time").textContent = fmtTime(o.sim_time);
  $("#last-scored").textContent = o.last_scored ? `· scored ${fmtTime(o.last_scored)}` : "· not scored yet";
  $("#predict-note").textContent = o.predict.trusted ? "" : "Risk % is from an experimental model and does not set status.";

  const k = o.kpis;
  $("#kpis").innerHTML = `
    <div class="card kpi"><div class="label">Assets</div>
      <div class="health-split">
        <span>${statusHtml("healthy")}<b>${k.healthy}</b></span>
        <span>${statusHtml("warning")}<b>${k.warning}</b></span>
        <span>${statusHtml("critical")}<b>${k.critical}</b></span>
      </div></div>
    <div class="card kpi"><div class="label">Open work orders</div><div class="value">${k.open_workorders}</div></div>
    <div class="card kpi"><div class="label">Predicted failures, next 30 days</div><div class="value">${k.predicted_failures_30d}</div>
      <div class="sub">${o.predict.trusted ? "risk above threshold" : "experimental model"}</div></div>
    <div class="card kpi"><div class="label">Alerts, last 7 days</div><div class="value">${k.alerts_7d}</div></div>`;

  $("#fleet").innerHTML = o.assets.map((a) => `
    <button class="card asset ${a.status}" data-asset="${esc(a.asset_id)}" type="button" aria-label="${esc(a.asset_id)}: ${STATUS[a.status].label}. Open details">
      <div class="name"><h3>${esc(a.asset_id)}</h3>${statusHtml(a.status)}</div>
      <div class="desc">${esc(a.description)}</div>
      <div><span class="risk">${pct(a.risk)}</span> <span class="muted">failure risk, 30 days${o.predict.trusted ? "" : " (experimental)"}</span></div>
      <dl>
        <dt>Top driver</dt><dd>${a.top_driver ? `<span class="tag-chip">${esc(a.top_driver)}</span>` : "—"}</dd>
        <dt>Open alerts</dt><dd>${a.open_alerts}</dd>
        <dt>Since last maintenance</dt><dd>${a.days_since_maintenance == null ? "none logged" : `${a.days_since_maintenance} days`}</dd>
      </dl>
    </button>`).join("");

  const sel = $("#asset-filter");
  if (sel.options.length === 1) for (const a of o.assets) sel.add(new Option(a.asset_id, a.asset_id));
  renderTables();
}

function renderTables() {
  const o = state.overview, f = state.filter;
  const alerts = o.alerts.filter((a) => !f || a.asset_id === f);
  $("#alerts-table tbody").innerHTML = alerts.length
    ? alerts.map((a) => `<tr><td>${esc(a.asset_id)}</td><td><span class="tag-chip">${esc(a.tag || a.kind)}</span></td>
        <td class="when">${fmtTime(a.first_flag_ts)}</td><td class="when">${fmtTime(a.last_flag_ts)}</td>
        <td class="num">${a.kind === "anomaly" ? Number(a.severity).toFixed(1) : pct(a.severity)}</td><td>${esc(a.interpretation)}</td></tr>`).join("")
    : `<tr><td colspan="6" class="empty">No alerts${f ? ` for ${esc(f)}` : ""}.</td></tr>`;
  const wos = o.workorders.filter((w) => !f || w.asset_id === f);
  $("#wo-table tbody").innerHTML = wos.length
    ? wos.map((w) => `<tr><td>${esc(w.id)}</td><td>${esc(w.asset_id)}</td><td>${esc(w.type)}</td><td class="when">${fmtTime(w.timestamp)}</td><td>${esc(w.status)}</td><td>${esc(w.description)}</td></tr>`).join("")
    : `<tr><td colspan="6" class="empty">No work orders${f ? ` for ${esc(f)}` : ""}.</td></tr>`;
}

// ---------------------------------------------------------------- asset drawer

async function openAsset(id) {
  state.openAsset = id;
  const drawer = $("#drawer");
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  $("#scrim").hidden = false;
  $("#drawer-title").textContent = id;
  $("#drawer-sub").textContent = "Loading…";
  $("#drawer-body").innerHTML = "";
  $("#close-drawer").focus();
  try {
    const d = await api(`/api/asset/${encodeURIComponent(id)}`);
    if (state.openAsset !== id) return;
    renderAsset(d);
  } catch (e) {
    $("#drawer-sub").textContent = `Could not load: ${e.message}`;
  }
}

function closeAsset() {
  state.openAsset = null;
  const drawer = $("#drawer");
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  $("#scrim").hidden = true;
}

function renderAsset(d) {
  const a = state.overview?.assets.find((x) => x.asset_id === d.asset_id);
  $("#drawer-sub").innerHTML = `${esc(a?.description ?? "")} · ${a ? statusHtml(a.status) : ""}`;
  const p = d.prediction;
  const pred = p
    ? `<div class="pred"><div class="row"><span class="risk">${pct(p.p_fail)}</span><span class="muted">probability of failure within ${p.horizon_days} days</span>
         ${p.trusted ? "" : '<span class="pill">experimental model</span>'}</div>
       <div>${esc(p.interpretation || "No documented symptom among the top drivers.")}</div>
       ${p.drivers.length ? `<div class="muted">Top drivers: ${p.drivers.map(([n]) => `<span class="tag-chip">${esc(n)}</span>`).join(", ")}</div>` : ""}
       <div class="muted">Features as of ${fmtTime(p.as_of)}, alert threshold ${pct(p.threshold)}.</div></div>`
    : `<p class="muted">No risk score yet. Run scoring from the demo controls.</p>`;
  const actions = `<ul class="plain">${d.actions.items.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
    <p class="muted">${d.actions.source === "playbook" ? "From the operator playbook." : "Interim guidance for " + esc(d.mode.replace(/_/g, " ")) + "; the operator playbook replaces it in Part E."}</p>`;
  const wos = d.workorders.length
    ? `<ul class="plain">${d.workorders.map((w) => `<li>${esc(w.id)} · ${esc(w.type)} · ${fmtTime(w.timestamp)} · ${esc(w.status)}${w.description ? ` — ${esc(w.description)}` : ""}</li>`).join("")}</ul>`
    : `<p class="muted">No work orders for this asset.</p>`;

  $("#drawer-body").innerHTML = `
    <section><h3>Current prediction</h3>${pred}</section>
    <section><h3>Recommended actions</h3>${actions}</section>
    <section>
      <div class="section-head"><h3>Last 30 days</h3><span class="legend-note"><i></i>alert period</span></div>
      <div class="charts" id="charts"></div>
    </section>
    <section>
      <h3>Create work order</h3>
      <form class="wo-form" id="wo-form">
        <div class="row">
          <label>Type <select name="type">
            <option>inspection</option><option>repair</option><option>replacement</option><option>lubrication</option><option>other</option>
          </select></label>
        </div>
        <textarea name="description" maxlength="500" placeholder="What should the crew do?" aria-label="Description"></textarea>
        <div class="row"><button class="btn secondary" type="submit">Review</button></div>
        <div id="wo-confirm"></div>
      </form>
      <h3 style="margin-top:14px">Work orders</h3>${wos}
    </section>`;

  const charts = $("#charts");
  const now = Date.parse(d.sim_time + "Z");
  for (const t of d.trends) charts.appendChild(chart(t, d.alerts, now));

  $("#wo-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    const draft = { asset_id: d.asset_id, type: fd.get("type"), description: String(fd.get("description") || "") };
    const box = $("#wo-confirm");
    try {
      const r = await api("/api/workorder", { method: "POST", body: JSON.stringify(draft) });
      box.innerHTML = `<div class="confirm"><div>Raise a <b>${esc(r.draft.type)}</b> work order on <b>${esc(r.draft.asset_id)}</b>${r.draft.description ? `: “${esc(r.draft.description)}”` : ""}?</div>
        <div class="row"><button class="btn" type="button" id="wo-yes">Confirm</button><button class="btn secondary" type="button" id="wo-no">Cancel</button></div></div>`;
      $("#wo-no").onclick = () => { box.innerHTML = ""; };
      $("#wo-yes").onclick = async () => {
        try {
          const wo = await api("/api/workorder", { method: "POST", body: JSON.stringify({ ...draft, confirm: true }) });
          box.innerHTML = `<p class="result">Created ${esc(wo.id)}.</p>`;
          await loadOverview();
          openAsset(d.asset_id);
        } catch (e) { box.innerHTML = `<p class="result error">${esc(e.message)}</p>`; }
      };
    } catch (e) { box.innerHTML = `<p class="result error">${esc(e.message)}</p>`; }
  });
}

// ---------------------------------------------------------------- chart (one tag, one axis)

function niceTicks(lo, hi, n = 3) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const step0 = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  // first tick at or below the minimum, last at or above the maximum: the line never leaves the axis
  const ticks = [];
  for (let v = Math.floor(lo / step) * step; ; v += step) {
    ticks.push(+v.toFixed(10));
    if (v >= hi - step * 1e-9) break;
  }
  return ticks;
}
const fmtVal = (v) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2));

function chart(trend, alerts, nowMs) {
  const wrap = document.createElement("div");
  wrap.className = "chart";
  const pts = trend.points.map(([t, v]) => [Date.parse(t + "Z"), v]);
  const vals = pts.map((p) => p[1]).filter((v) => v !== null);
  const last = [...pts].reverse().find((p) => p[1] !== null);
  wrap.innerHTML = `<h4>${esc(trend.tag)}<span>${trend.dead ? "sensor reads a constant (dead)" : last ? `now ${fmtVal(last[1])}` : "no data"}</span></h4>`;
  if (!vals.length) return wrap;

  const W = 600, H = 130, L = 44, R = 8, T = 8, B = 20;
  const x0 = pts[0][0], x1 = pts[pts.length - 1][0];
  const ticks = niceTicks(Math.min(...vals), Math.max(...vals));
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const X = (t) => L + ((t - x0) / Math.max(x1 - x0, 1)) * (W - L - R);
  const Y = (v) => T + (1 - (v - y0) / Math.max(y1 - y0, 1e-9)) * (H - T - B);

  let path = "", pen = false;
  for (const [t, v] of pts) {
    if (v === null) { pen = false; continue; }
    path += `${pen ? "L" : "M"}${X(t).toFixed(1)},${Y(v).toFixed(1)}`;
    pen = true;
  }
  const bands = alerts.filter((a) => a.tag === trend.tag).map((a) => {
    const f = Math.max(Date.parse(a.first_flag_ts + "Z"), x0), l = Math.min(Date.parse(a.last_flag_ts + "Z"), x1);
    if (l < x0 || f > x1) return "";
    return `<rect class="band" x="${X(f)}" y="${T}" width="${Math.max(X(l) - X(f), 2)}" height="${H - T - B}"><title>Alert ${fmtTime(a.first_flag_ts)} to ${fmtTime(a.last_flag_ts)}, peak z ${Number(a.severity).toFixed(1)}</title></rect>
            <line class="band-edge" x1="${X(f)}" x2="${X(f)}" y1="${T}" y2="${H - B}"/>`;
  }).join("");
  const xt = [0, 1, 2, 3].map((i) => x0 + ((x1 - x0) * i) / 3);
  const svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(trend.tag)}, last 30 days, from ${fmtVal(Math.min(...vals))} to ${fmtVal(Math.max(...vals))}${last ? `, now ${fmtVal(last[1])}` : ""}">
    ${ticks.map((v) => `<line class="gridline" x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/><text class="tick" x="${L - 6}" y="${Y(v) + 3}" text-anchor="end">${fmtVal(v)}</text>`).join("")}
    ${bands}
    <path class="line" d="${path}"/>
    ${xt.map((t, i) => `<text class="tick" x="${X(t)}" y="${H - 5}" text-anchor="${i === 0 ? "start" : i === 3 ? "end" : "middle"}">${fmtDate(new Date(t).toISOString().slice(0, 19))}</text>`).join("")}
    <line class="cross" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>
    <circle class="hover-dot" r="4" visibility="hidden"/>
    <rect x="${L}" y="0" width="${W - L - R}" height="${H}" fill="transparent" class="hit"/>
  </svg>`;
  wrap.insertAdjacentHTML("beforeend", svg);

  const el = wrap.querySelector("svg"), cross = el.querySelector(".cross"), dot = el.querySelector(".hover-dot"), tip = $("#tooltip");
  const move = (ev) => {
    const box = el.getBoundingClientRect();
    const px = ((ev.clientX - box.left) / box.width) * W;
    const t = x0 + ((px - L) / (W - L - R)) * (x1 - x0);
    let best = null;
    for (const p of pts) if (p[1] !== null && (!best || Math.abs(p[0] - t) < Math.abs(best[0] - t))) best = p;
    if (!best) return;
    cross.setAttribute("x1", X(best[0])); cross.setAttribute("x2", X(best[0])); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", X(best[0])); dot.setAttribute("cy", Y(best[1])); dot.setAttribute("visibility", "visible");
    tip.hidden = false;
    tip.textContent = `${fmtTime(new Date(best[0]).toISOString().slice(0, 19))} · ${fmtVal(best[1])}`;
    tip.style.left = `${Math.min(ev.clientX + 12, window.innerWidth - tip.offsetWidth - 8)}px`;
    tip.style.top = `${ev.clientY - 36}px`;
  };
  const leave = () => { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.hidden = true; };
  el.addEventListener("pointermove", move);
  el.addEventListener("pointerleave", leave);
  return wrap;
}

// ---------------------------------------------------------------- demo controls

async function runDemo(action, body = {}) {
  const out = $("#demo-result");
  out.className = "result";
  out.textContent = "Working…";
  for (const b of document.querySelectorAll("#demo button")) b.disabled = true;
  try {
    const r = await api(`/api/demo/${action}`, { method: "POST", body: JSON.stringify(body) });
    out.textContent = describe(action, r.result);
    await loadOverview();
    if (state.openAsset) openAsset(state.openAsset);
  } catch (e) {
    out.className = "result error";
    out.textContent = e.message;
  } finally {
    for (const b of document.querySelectorAll("#demo button")) b.disabled = false;
  }
}

function describe(action, r) {
  if (action === "jump7" || action === "jump") return `Clock moved to ${fmtTime(r.sim_time)}. Run scoring to see new alerts.`;
  if (action === "score") return r.skipped ? `Already scored for this sim day (${fmtDate(r.as_of)}).` : `Scored as of ${fmtTime(r.as_of)}: ${r.anomaly?.flags ?? 0} anomaly flag(s).`;
  if (action === "inject") return `Fault injected on ${r.injected?.asset ?? "the asset"}; symptoms build up over the next days.`;
  if (action === "reset") return `Plant reset to ${fmtTime(r.sim_time)}; scoring results cleared.`;
  return "Done.";
}

// ---------------------------------------------------------------- wiring

document.addEventListener("DOMContentLoaded", () => {
  $("#fleet").addEventListener("click", (ev) => {
    const card = ev.target.closest("[data-asset]");
    if (card) openAsset(card.dataset.asset);
  });
  $("#close-drawer").addEventListener("click", closeAsset);
  $("#scrim").addEventListener("click", closeAsset);
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && state.openAsset) closeAsset(); });
  $("#asset-filter").addEventListener("change", (ev) => { state.filter = ev.target.value; renderTables(); });

  const dlg = $("#demo");
  $("#open-demo").addEventListener("click", () => { $("#demo-result").textContent = ""; dlg.showModal(); });
  for (const b of document.querySelectorAll("[data-demo]")) {
    b.addEventListener("click", () => {
      const action = b.dataset.demo;
      if (action === "reset" && !b.dataset.armed) {
        b.dataset.armed = "1";
        b.textContent = "Click again to reset for everyone";
        setTimeout(() => { delete b.dataset.armed; b.textContent = "Reset plant"; }, 4000);
        return;
      }
      delete b.dataset.armed;
      if (action === "reset") b.textContent = "Reset plant";
      runDemo(action);
    });
  }
  $("#jump-form").addEventListener("submit", (ev) => { ev.preventDefault(); runDemo("jump", { day: Number($("#jump-day").value) }); });
  $("#inject-form").addEventListener("submit", (ev) => { ev.preventDefault(); runDemo("inject", { asset_id: $("#inject-asset").value }); });

  loadOverview();
  setInterval(loadOverview, 60_000);
});

// ---------------------------------------------------------------- chat (Part E assistant)

const TOOL_LABEL = {
  get_asset_status: "checked asset status", get_events: "read the event history",
  get_recommendations: "read the playbook", create_workorder: "drafted a work order",
};

function sessionId() {
  let id = null;
  try { id = sessionStorage.getItem("plant-chat-session"); } catch { /* private mode */ }
  if (!id || !/^[A-Za-z0-9_-]{8,64}$/.test(id)) {
    id = "web-" + Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, "0")).join("");
    try { sessionStorage.setItem("plant-chat-session", id); } catch { /* fine: one session per page load */ }
  }
  return id;
}

// Escape first, then a small safe subset of Markdown: paragraphs, bullets, **bold**, `code`.
function renderMarkdown(src) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [];
  let list = null;
  for (const line of src.split("\n")) {
    const m = /^\s*[-*]\s+(.*)$/.exec(line);
    if (m) { (list ??= []).push(`<li>${inline(m[1])}</li>`); continue; }
    if (list) { out.push(`<ul>${list.join("")}</ul>`); list = null; }
    if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`<ul>${list.join("")}</ul>`);
  return out.join("");
}

function addMsg(cls, html) {
  const el = document.createElement("div");
  el.className = `msg ${cls}`;
  el.innerHTML = html;
  $("#chat-log").appendChild(el);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  return el;
}

function draftCard(d) {
  const card = document.createElement("div");
  card.className = "draft-card";
  card.innerHTML = `<div><strong>Draft work order</strong> · ${esc(d.asset_id)} · ${esc(d.work_type)}${d.scheduled_for ? ` · ${fmtTime(d.scheduled_for)}` : ""}</div>
    <div>${esc(d.description)}</div>
    <div class="row"><button class="btn" type="button">Confirm</button><button class="btn secondary" type="button">Cancel</button></div>`;
  const [yes, no] = card.querySelectorAll("button");
  no.onclick = () => { card.innerHTML = '<span class="muted">Draft discarded.</span>'; };
  yes.onclick = async () => {
    yes.disabled = no.disabled = true;
    try {
      const wo = await api("/api/chat/confirm", { method: "POST", body: JSON.stringify({ session_id: sessionId(), draft_id: d.draft_id }) });
      card.innerHTML = `<span>Created <strong>${esc(wo.id)}</strong> on ${esc(d.asset_id)}.</span>`;
      loadOverview();
    } catch (e) {
      card.insertAdjacentHTML("beforeend", `<p class="result error">${esc(e.message)}</p>`);
      yes.disabled = no.disabled = false;
    }
  };
  return card;
}

async function sendChat(message) {
  const input = $("#chat-input"), send = $("#chat-form button");
  addMsg("user", esc(message));
  const bot = addMsg("bot", '<div class="tools"></div><div class="body"><span class="muted">Thinking…</span></div>');
  const toolsEl = bot.querySelector(".tools"), body = bot.querySelector(".body");
  input.disabled = send.disabled = true;
  let text = "", tools = [];
  try {
    const r = await fetch("/api/chat", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ message, session_id: sessionId() }) });
    if (!r.ok) {
      const detail = (await r.json().catch(() => null))?.detail;
      throw new Error(detail || `HTTP ${r.status}`);
    }
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.type === "text") { text += ev.text; body.innerHTML = renderMarkdown(text); }
        else if (ev.type === "tool") { tools.push(TOOL_LABEL[ev.name] || ev.name); toolsEl.textContent = [...new Set(tools)].join(" · "); }
        else if (ev.type === "draft") { bot.appendChild(draftCard(ev)); }
        else if (ev.type === "error") { bot.classList.add("error"); body.innerHTML += `<p>${esc(ev.message)}</p>`; }
        $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
      }
    }
    if (!text && !bot.classList.contains("error")) body.innerHTML = '<span class="muted">No answer.</span>';
  } catch (e) {
    bot.classList.add("error");
    body.innerHTML = `<p>${esc(e.message)}</p>`;
  } finally {
    input.disabled = send.disabled = false;
    input.focus();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const panel = $("#chat"), toggle = $("#chat-toggle");
  const setOpen = (open) => { panel.classList.toggle("open", open); toggle.setAttribute("aria-expanded", String(open)); toggle.hidden = open; if (open) $("#chat-input").focus(); };
  toggle.addEventListener("click", () => setOpen(true));
  $("#chat-close").addEventListener("click", () => setOpen(false));
  $("#chat-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const msg = $("#chat-input").value.trim();
    if (!msg) return;
    $("#chat-input").value = "";
    sendChat(msg);
  });
  for (const chip of document.querySelectorAll(".chip")) chip.addEventListener("click", () => sendChat(chip.textContent));
});
