"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const state = {
  prereqs: null,
  tools: [],
  active: null,          // console tool id
  since: {},             // tool id -> last log seq seen
  clientClear: {},       // tool id -> seq below which to hide (Clear button)
  autoscroll: true,
  rendered: false,
  revision: null,        // disk-watcher revision; bumps when files change on disk
  assetVersion: null,    // UI files version; reloads the app when the folder's UI changes
};

async function api(method, path, body) {
  const opt = { method };
  if (body) { opt.headers = { "Content-Type": "application/json" }; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  return r.json().catch(() => ({}));
}

function toast(msg, kind = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast " + kind;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 3400);
}

function fmtAgo(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

// --------------------------------------------------------------------------- //
//  Bootstrap + render
// --------------------------------------------------------------------------- //
async function bootstrap() {
  const data = await api("GET", "/api/bootstrap");
  state.prereqs = data.prereqs;
  state.tools = data.tools;
  state.revision = data.revision;
  $("#hostInfo").textContent = data.launcher_url || "";
  if (!state.active && state.tools.length) state.active = state.tools[0].id;
  renderPrereqs();
  renderFooter();
  renderTabs();
  renderCards();
}

function renderPrereqs() {
  const p = state.prereqs, bar = $("#prereqBar");
  const problems = [];
  if (!p.git.ok) problems.push("Git is not installed. Install <a href='https://git-scm.com/download/win' target='_blank'>Git for Windows</a>, then restart the launcher.");
  if (!p.python.ok) problems.push("Python 3.10 was not found. Re-run <code>Start.bat</code> (it can install it), or install Python 3.10 manually.");
  if (p.onedrive_warning) problems.push("⚠ Your install folder is inside <b>OneDrive</b> — multi-GB models will corrupt or sync slowly. Open ⚙ Settings and change it to e.g. <code>C:\\AItools</code>.");
  if (problems.length) {
    bar.classList.remove("hidden", "ok");
    bar.innerHTML = "🛠 " + problems.join(" &nbsp;·&nbsp; ");
  } else {
    bar.classList.add("hidden");
  }
}

function renderFooter() {
  $("#installRoot").textContent = state.prereqs.install_root;
}

function badgeFor(t) {
  switch (t.state) {
    case "installing": return ["busy", "Installing…"];
    case "updating": return ["busy", "Updating…"];
    case "stopping": return ["busy", "Stopping…"];
    case "running": return t.ready ? ["running", "Running"] : ["busy", "Starting…"];
  }
  if (!t.installed) return ["notinstalled", "Not installed"];
  if (t.update && t.update.behind > 0) return ["update", "Update available"];
  return ["installed", "Installed"];
}

function cardButtons(t) {
  const p = state.prereqs;
  const repoBtn = `<a class="btn btn-tiny btn-ghost" href="${esc(t.repo)}" target="_blank" title="Open GitHub repo">GitHub ↗</a>`;
  const busy = ["installing", "updating", "stopping"].includes(t.state);
  const scanBtn = `<button class="btn btn-ghost btn-tiny" data-act="scan" data-id="${t.id}" ${t.scanning ? "disabled" : ""} title="Read-only safety scan of this tool's files">
      <span class="${t.scanning ? "spin" : ""}">🛡</span> ${t.scanning ? "Scanning…" : "Scan"}</button>`;
  const customBtns = t.custom ? `
      <button class="btn btn-ghost btn-tiny" data-act="runconfig" data-id="${t.id}" title="Set or override the run command">⚙ Run config</button>
      <button class="btn btn-ghost btn-tiny" data-act="remove" data-id="${t.id}" title="Remove this tool">🗑</button>` : "";

  if (!t.installed) {
    const blocked = !p.git.ok || !p.python.ok;
    const tip = blocked ? "Install Git and Python first (see banner above)" : "Clone & set up this tool";
    return `
      <button class="btn btn-primary" data-act="install" data-id="${t.id}" ${blocked || busy ? "disabled" : ""} title="${tip}">
        <span class="${t.state === "installing" ? "spin" : ""}">⬇</span> ${t.custom ? "Download & set up" : "Install"}</button>
      ${customBtns}
      ${repoBtn}`;
  }

  if (t.state === "running") {
    return `
      <button class="btn btn-primary" data-act="open" data-id="${t.id}" ${t.ready ? "" : "disabled"} title="Open the tool's web UI">
        ${t.ready ? "🌐 Open UI" : "⏳ Starting…"}</button>
      <button class="btn btn-danger" data-act="stop" data-id="${t.id}">⏹ Stop</button>
      ${scanBtn}
      ${repoBtn}`;
  }

  const hasUpdate = t.update && t.update.behind > 0;
  return `
    <button class="btn btn-success" data-act="launch" data-id="${t.id}" ${busy ? "disabled" : ""}>▶ Launch</button>
    <button class="btn ${hasUpdate ? "btn-warn" : "btn-ghost"}" data-act="update" data-id="${t.id}" ${busy ? "disabled" : ""} title="git pull + reinstall changed deps">
      <span class="${t.state === "updating" ? "spin" : ""}">⟳</span> Update${hasUpdate ? ` (${t.update.behind})` : ""}</button>
    <button class="btn btn-ghost btn-tiny" data-act="reveal" data-id="${t.id}" title="Open install folder">📁</button>
    ${scanBtn}
    ${customBtns}
    ${repoBtn}`;
}

function cardSecurity(t) {
  if (t.scanning) return `<div class="sec-line scanning">🛡 <span class="spin">⟳</span> Scanning files…</div>`;
  const s = t.scan;
  if (!s || !s.done) return "";
  const review = (s.medium || 0) + (s.pickles || 0);
  if (s.verdict === "danger")
    return `<div class="sec-line danger" data-act="scanreport" data-id="${t.id}" title="View the security report">
      ⚠ ${s.high} high-risk signal${s.high === 1 ? "" : "s"} found — click to review</div>`;
  if (s.verdict === "error")
    return `<div class="sec-line warn" data-act="scanreport" data-id="${t.id}">🛡 Scan couldn't finish — click for details</div>`;
  return `<div class="sec-line ok" data-act="scanreport" data-id="${t.id}" title="View the security report">
    🛡 No high-risk signals${review ? ` · ${review} to review` : ""}</div>`;
}

function cardMeta(t) {
  const bits = [];
  if (t.custom && t.kind && t.kind !== "unknown")
    bits.push(`<span class="kind-chip">${esc(t.kind)}</span>`);
  if (t.installed) {
    if (t.commit && t.commit.short)
      bits.push(`branch <code>${esc(t.commit.branch || "?")}</code>`);
    if (t.commit && t.commit.short)
      bits.push(`<code>${esc(t.commit.short)}</code>`);
    if (t.update && t.update.checked)
      bits.push(`checked ${fmtAgo(t.update.checked)}`);
  }
  bits.push(t.port ? `port <code>${t.port}</code>` : `port <code>auto</code>`);
  return bits.join(" · ");
}

function cardNote(t) {
  if (t.error)
    return `<div class="update-note" style="color:#ffd9d9;border-color:rgba(248,113,113,.3);background:rgba(248,113,113,.07)">⚠ ${esc(t.error)}</div>`;
  if (t.custom && t.installed && !t.run_cmd)
    return `<div class="update-note">⚙ No run command set — open <b>Run config</b> to tell it how to start.</div>`;
  if (t.installed && t.update && t.update.behind > 0)
    return `<div class="update-note">⬆ ${t.update.behind} new commit(s) upstream${t.update.subject ? ` — latest: “${esc(t.update.subject)}”` : ""}</div>`;
  if (t.installed && t.update && t.update.ahead > 0)
    return `<div class="card-meta" style="margin-top:8px">↳ ${t.update.ahead} local commit(s) ahead of upstream</div>`;
  if (!t.installed && t.first_launch_installs)
    return `<div class="card-meta" style="margin-top:8px">ℹ First launch downloads large dependencies/models.</div>`;
  if (t.installed && t.auto_update_on_launch)
    return `<div class="card-meta" style="margin-top:8px">ℹ Also self-updates each time it launches.</div>`;
  return "";
}

function renderCards() {
  const wrap = $("#cards");
  wrap.innerHTML = state.tools.map(t => {
    const [bcls, blabel] = badgeFor(t);
    return `
    <div class="card" style="--tool-color:${t.color}">
      <div class="card-top">
        <div class="card-icon">${t.emoji}</div>
        <div class="card-title">
          <h3>${esc(t.name)}</h3>
          <p${t.description_from_github ? ' title="Synced from this repo’s GitHub “About”"' : ''}>${esc(t.description)}${t.description_from_github ? ' <span class="gh-synced" title="This intro is synced live from the repo’s GitHub About">↻ GitHub</span>' : ''}</p>
        </div>
        <span class="badge ${bcls}"><span class="dot"></span>${blabel}</span>
      </div>
      <div class="card-meta">${cardMeta(t)}</div>
      ${cardNote(t)}
      ${cardSecurity(t)}
      <div class="card-actions">${cardButtons(t)}</div>
    </div>`;
  }).join("");

  $$("#cards [data-act]").forEach(b => b.addEventListener("click", onAction));
  state.rendered = true;
}

function renderTabs() {
  const tabs = $("#consoleTabs");
  tabs.innerHTML = state.tools.map(t => {
    const live = ["installing", "updating", "running", "stopping"].includes(t.state);
    return `<button class="console-tab ${t.id === state.active ? "active" : ""} ${live ? "live" : ""}" data-tab="${t.id}">
      <span class="tab-dot"></span>${t.emoji} ${esc(t.name)}</button>`;
  }).join("");
  $$("#consoleTabs [data-tab]").forEach(b =>
    b.addEventListener("click", () => { state.active = b.dataset.tab; renderTabs(); refreshLog(true); }));
}

// --------------------------------------------------------------------------- //
//  Actions
// --------------------------------------------------------------------------- //
async function onAction(e) {
  const btn = e.currentTarget;
  const id = btn.dataset.id, act = btn.dataset.act;
  const tool = state.tools.find(t => t.id === id);
  state.active = id;
  renderTabs();

  if (act === "open") { window.open(tool.url, "_blank"); return; }
  if (act === "reveal") { api("POST", "/api/reveal", { tool: id }); return; }
  if (act === "scanreport") { openSecurityReport(id); return; }
  if (act === "runconfig") { openRunConfig(id); return; }
  if (act === "remove") {
    const okr = await confirmDialog("Remove " + tool.name + "?",
      "This permanently deletes the tool and ALL its downloaded files from your computer (" +
      "the folder in your install root). This can't be undone.");
    if (!okr) return;
    const res = await api("POST", "/api/remove_tool", { tool: id, delete_files: true });
    if (res.ok) {
      const locked = res.message && res.message.indexOf("locked") !== -1;
      toast(locked ? res.message : (tool.name + " deleted"), locked ? "err" : "ok");
      if (state.active === id) state.active = (state.tools[0] || {}).id || null;
    } else toast(res.message || "Couldn't remove", "err");
    bootstrap();
    return;
  }

  if (act === "install" && tool.first_launch_installs) {
    const ok = await confirmDialog("Install " + tool.name + "?",
      tool.id === "fooocus"
        ? "Fooocus downloads ~30 GB of default SDXL models on its first launch. Make sure you have the disk space. Continue?"
        : "This tool downloads several GB of dependencies on its first launch. Continue?");
    if (!ok) return;
  }

  btn.disabled = true;
  const res = await api("POST", "/api/" + act, { tool: id });
  if (!res.ok) { toast(res.message || res.error || "Failed", "err"); }
  else {
    const verb = { install: "Installing", update: "Updating", launch: "Launching", stop: "Stopping", scan: "Scanning" }[act];
    if (verb) toast(verb + " " + tool.name + "…", "ok");
  }
  pollStatus();
  refreshLog(true);
}

// Live-follow the source folder: reload the UI when its web files are edited.
async function checkLiveReload() {
  try {
    const d = await api("GET", "/api/version");
    if (d.version == null) return;
    if (state.assetVersion == null) { state.assetVersion = d.version; return; }
    if (d.version !== state.assetVersion) {
      state.assetVersion = d.version;
      toast("Updated from the folder — reloading…", "ok");
      setTimeout(() => location.reload(), 500);
    }
  } catch (e) { /* ignore */ }
}

async function checkAll() {
  const btn = $("#checkAllBtn");
  btn.classList.add("is-spinning");
  await api("POST", "/api/check", { all: true });
  toast("Checking all tools for updates…", "ok");
  setTimeout(() => { pollStatus(); btn.classList.remove("is-spinning"); }, 2500);
}

// --------------------------------------------------------------------------- //
//  Polling
// --------------------------------------------------------------------------- //
async function pollStatus() {
  try {
    const data = await api("GET", "/api/status");
    if (!data.tools) return;
    state.tools = data.tools;
    renderTabs();
    renderCards();
    renderPrereqs();
    if (data.revision != null && state.revision != null &&
        data.revision !== state.revision) {
      toast("Updated — refreshed.", "ok");
      bootstrap();   // refresh prereqs/footer too (install folder may have changed)
    }
    if (data.revision != null) state.revision = data.revision;
  } catch (e) { /* ignore transient */ }
}

async function refreshLog(reset = false) {
  const id = state.active;
  if (!id) return;
  if (reset) state.since[id] = state.clientClear[id] || 0;
  const since = state.since[id] || 0;
  try {
    const data = await api("GET", `/api/log?tool=${encodeURIComponent(id)}&since=${since}`);
    const pre = $("#console");
    if (reset) pre.innerHTML = "";
    if (data.lines && data.lines.length) {
      const clear = state.clientClear[id] || 0;
      const frag = document.createDocumentFragment();
      for (const ln of data.lines) {
        if (ln.seq <= clear) continue;
        const span = document.createElement("span");
        span.className = "ln-" + ln.kind;
        span.textContent = ln.text + "\n";
        frag.appendChild(span);
      }
      pre.appendChild(frag);
      state.since[id] = data.seq;
      if (state.autoscroll) pre.scrollTop = pre.scrollHeight;
    } else if (reset && pre.innerHTML === "") {
      pre.innerHTML = `<span class="muted">No output yet for this tool. Run an action to see live logs.</span>`;
    }
  } catch (e) { /* ignore */ }
}

// --------------------------------------------------------------------------- //
//  Modals / settings
// --------------------------------------------------------------------------- //
function openModal(sel) { $(sel).classList.remove("hidden"); }
function closeModal(sel) { $(sel).classList.add("hidden"); }

function confirmDialog(title, msg) {
  return new Promise(resolve => {
    $("#confirmTitle").textContent = title;
    $("#confirmMsg").textContent = msg;
    openModal("#confirmModal");
    const ok = $("#confirmOkBtn");
    const done = (val) => {
      closeModal("#confirmModal");
      ok.removeEventListener("click", onOk);
      resolve(val);
    };
    const onOk = () => done(true);
    ok.addEventListener("click", onOk);
    $$("#confirmModal [data-close]").forEach(b =>
      b.addEventListener("click", () => done(false), { once: true }));
  });
}

function openSettings() {
  const p = state.prereqs;
  $("#installRootInput").value = p.install_root;
  $("#envInfo").innerHTML = `
    <span class="k">Git</span><span class="v ${p.git.ok ? "good" : "bad"}">${esc(p.git.version || "missing")}</span>
    <span class="k">Python 3.10</span><span class="v ${p.python.ok ? "good" : "bad"}">${esc(p.python.version || "missing")}</span>
    <span class="k">Python path</span><span class="v">${esc(p.python.path || "—")}</span>
    <span class="k">OneDrive</span><span class="v ${p.onedrive_warning ? "bad" : "good"}">${p.onedrive_warning ? "install path is synced (bad)" : "ok (not synced)"}</span>`;
  openSettings._lastRoot = p.install_root;
  openModal("#settingsModal");
}

async function saveSettings() {
  const root = $("#installRootInput").value.trim();
  if (!root) { toast("Enter a folder path", "err"); return; }
  const res = await api("POST", "/api/config", { install_root: root });
  if (res.ok) {
    state.prereqs = res.prereqs;
    state.tools = res.tools;
    closeModal("#settingsModal");
    renderPrereqs(); renderFooter(); renderCards(); renderTabs();
    toast("Install folder updated", "ok");
  } else {
    toast(res.error || "Could not save", "err");
  }
}

async function openSecurityReport(id) {
  const tool = state.tools.find(t => t.id === id) || {};
  $("#securityTitle").textContent = `🛡 Security report — ${tool.name || id}`;
  const body = $("#securityBody");
  body.innerHTML = `<p class="muted">Loading…</p>`;
  openModal("#securityModal");
  const data = await api("GET", `/api/scan_report?tool=${encodeURIComponent(id)}`);
  const s = data.scan || {};
  if (data.scanning && !s.done) { body.innerHTML = `<p class="muted">Scan in progress… reopen when it finishes.</p>`; return; }
  if (!s.done) { body.innerHTML = `<p class="muted">No scan yet — click “🛡 Scan” on the card.</p>`; return; }

  const findings = s.findings || [];
  const high = findings.filter(f => f.severity === "high");
  const med = findings.filter(f => f.severity === "medium");
  const when = s.when ? fmtAgo(s.when) : "";
  const verdictBox = s.verdict === "danger"
    ? `<div class="sec-summary danger">⚠ <b>${s.high}</b> high-risk signal(s) found. Review the items below carefully <b>before launching</b> this tool.</div>`
    : s.verdict === "error"
      ? `<div class="sec-summary warn">Scan didn't finish${s.error ? `: ${esc(s.error)}` : "."}</div>`
      : `<div class="sec-summary ok">🛡 No high-risk signals found. The items below are normal capabilities of these tools (they run code & load models) — shown for transparency, not alarms.</div>`;
  const stat = `<div class="sec-stats">Scanned <b>${s.scanned}</b> code files · <b>${s.high}</b> high · <b>${s.medium}</b> review · <b>${s.pickles}</b> pickle model file(s) · ${when}</div>`;

  const group = (title, items, cls) => {
    if (!items.length) return "";
    const rows = items.slice(0, 250).map(f => `
      <div class="finding ${cls}">
        <div class="finding-file">${esc(f.file)}${f.line ? ":" + f.line : ""}</div>
        <div class="finding-reason">${esc(f.reason)}</div>
        ${f.snippet ? `<code class="finding-snippet">${esc(f.snippet)}</code>` : ""}
      </div>`).join("");
    return `<h3 class="sec-group ${cls}">${title} (${items.length})</h3>${rows}`;
  };
  body.innerHTML = verdictBox + stat +
    group("🔴 High-risk", high, "high") +
    group("🟡 Review", med, "medium") +
    (high.length + med.length === 0 ? `<p class="muted">Nothing flagged.</p>` : "");
}

// --------------------------------------------------------------------------- //
//  Add a repository + run config
// --------------------------------------------------------------------------- //
function openAddModal() {
  $("#addRepoInput").value = "";
  $("#addNameInput").value = "";
  const st = $("#addStatus"); st.classList.add("hidden"); st.textContent = "";
  openModal("#addModal");
  setTimeout(() => $("#addRepoInput").focus(), 60);
}

async function submitAddTool() {
  const repo = $("#addRepoInput").value.trim();
  const name = $("#addNameInput").value.trim();
  if (!repo) { toast("Paste a repository link", "err"); return; }
  const st = $("#addStatus");
  st.classList.remove("hidden");
  st.className = "add-status";
  st.textContent = "Checking the repository…";
  const btn = $("#addOkBtn"); btn.disabled = true;
  const res = await api("POST", "/api/add_tool", { repo_url: repo, name });
  btn.disabled = false;
  if (res.ok) {
    closeModal("#addModal");
    toast("Added — now click “Download & set up” on its card", "ok");
    if (res.tool) state.active = res.tool;
    await bootstrap();
  } else {
    st.className = "add-status err";
    st.textContent = res.message || "Couldn't add that repository.";
  }
}

function openRunConfig(id) {
  const t = state.tools.find(x => x.id === id) || {};
  $("#runcfgTitle").textContent = "⚙ Run config — " + (t.name || id);
  $("#runcfgCmd").value = (t.run_cmd || "").replace(/\$\{venv_python\}/g, "python").replace(/\$\{python\}/g, "python").replace(/\$\{port\}/g, t.port || "PORT");
  $("#runcfgPort").value = t.port ? t.port : "";
  $("#runcfgPip").value = "";
  openRunConfig._id = id;
  openModal("#runcfgModal");
}

async function saveRunConfig() {
  const id = openRunConfig._id;
  const body = {
    tool: id,
    run_cmd: $("#runcfgCmd").value.trim() || null,
    port: $("#runcfgPort").value.trim() || null,
    pip: $("#runcfgPip").value.trim() || null,
  };
  const res = await api("POST", "/api/configure", body);
  if (res.ok) { closeModal("#runcfgModal"); toast("Saved", "ok"); pollStatus(); }
  else toast(res.message || "Couldn't save", "err");
}

// --------------------------------------------------------------------------- //
//  Wire up
// --------------------------------------------------------------------------- //
function init() {
  $("#checkAllBtn").addEventListener("click", checkAll);
  $("#settingsBtn").addEventListener("click", openSettings);
  $("#saveSettingsBtn").addEventListener("click", saveSettings);
  $("#addToolBtn").addEventListener("click", openAddModal);
  $("#addOkBtn").addEventListener("click", submitAddTool);
  $("#addRepoInput").addEventListener("keydown", e => { if (e.key === "Enter") submitAddTool(); });
  $("#runcfgOkBtn").addEventListener("click", saveRunConfig);
  $$("[data-close]").forEach(b => b.addEventListener("click", e => {
    const m = e.target.closest(".modal"); if (m) m.classList.add("hidden");
  }));
  $("#clearLogBtn").addEventListener("click", () => {
    const id = state.active;
    if (!id) return;
    state.clientClear[id] = state.since[id] || 0;
    $("#console").innerHTML = `<span class="muted">— cleared —</span>\n`;
  });
  $("#autoscrollBtn").addEventListener("click", (e) => {
    state.autoscroll = !state.autoscroll;
    e.currentTarget.classList.toggle("is-on", state.autoscroll);
    e.currentTarget.textContent = "Auto-scroll " + (state.autoscroll ? "✓" : "✗");
  });

  bootstrap().then(() => refreshLog(true));
  setInterval(pollStatus, 1600);
  setInterval(() => refreshLog(false), 900);
  setInterval(checkLiveReload, 1700);
}

document.addEventListener("DOMContentLoaded", init);
