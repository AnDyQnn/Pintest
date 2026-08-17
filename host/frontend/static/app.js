// app.js — логика дашборда: графика (fx.js), авторизация, вкладки, живой статус, действия.
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])));

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 401) { showLogin(); throw new Error("нужна авторизация"); }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || r.statusText);
  return data;
}

// ── графика (fx.js) ────────────────────────────────────────────────────────
function initFx() {
  if (!window.GWFX) return;
  GWFX.background(document.getElementById("fx-bg"), "hi");
  GWFX.favicon();
  GWFX.icons(document);
  const shield = document.getElementById("topbar-shield");
  if (shield && GWFX.runShield) GWFX.runShield(shield);
}
function initLoginLogo() {
  const c = document.getElementById("login-logo");
  if (c && window.GWFX && GWFX.runShield) GWFX.runShield(c);   // анимированный щит + бренд PINTEST рядом
}
// часы
setInterval(() => { const c = $("#clock"); if (c) c.textContent = new Date().toLocaleTimeString("ru-RU"); }, 1000);

// ── авторизация ─────────────────────────────────────────────────────────────
function showLogin() { $("#login").classList.remove("hidden"); $("#app").classList.add("hidden"); }
function showApp() { $("#login").classList.add("hidden"); $("#app").classList.remove("hidden"); GWFX && GWFX.icons(document); startLive(); loadOverview(); }
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/login", { method: "POST", body: { user: $("#login-user").value, password: $("#login-pass").value } });
    showApp();
  } catch (err) { $("#login-err").textContent = err.message; }
});
$("#logout").addEventListener("click", async () => { try { await api("/logout", { method: "POST" }); } catch (e) {} showLogin(); });

// ── вкладки ───────────────────────────────────────────────────────────────
const TAB_LOADERS = {};
$$(".topbar nav a").forEach((t) => t.addEventListener("click", (e) => {
  e.preventDefault();
  $$(".topbar nav a").forEach((x) => x.classList.remove("active"));
  $$(".tab").forEach((x) => x.classList.remove("active"));
  t.classList.add("active");
  $("#tab-" + t.dataset.tab).classList.add("active");
  if (TAB_LOADERS[t.dataset.tab]) TAB_LOADERS[t.dataset.tab]();
}));

// ── живой статус (WS) ───────────────────────────────────────────────────────
let ws = null, lastLive = { agents: [] };
const HIST = { agents: [], cve: [], captured: [], peers: [] };
function startLive() {
  if (ws) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/api/live`);
  ws.onmessage = (ev) => { try { lastLive = JSON.parse(ev.data); renderLive(lastLive); } catch (e) {} };
  ws.onclose = () => { ws = null; setTimeout(startLive, 2000); };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}
function spark(id, arr, color) {
  const cv = document.getElementById(id); if (!cv) return;
  const ctx = cv.getContext("2d"), w = cv.width, h = cv.height; ctx.clearRect(0, 0, w, h);
  if (!arr.length) return;
  const mx = Math.max(1, ...arr), n = arr.length, step = w / Math.max(1, n - 1);
  ctx.beginPath();
  arr.forEach((v, i) => { const x = i * step, y = h - (v / mx) * (h - 4) - 2; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.stroke();
  ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
  ctx.fillStyle = color + "22"; ctx.fill();
}
function renderLive(d) {
  renderTopology($("#topology"), d);
  const a = d.agents || [], cnt = (s) => a.filter((x) => x.status === s).length;
  $("#topo-legend").textContent = `· на связи ${cnt("online")} · потеряно ${cnt("lost")} · уничтожено ${cnt("destroyed")}`;
  const peers = (d.vpn && d.vpn.peer_count) || 0;
  push(HIST.agents, cnt("online")); push(HIST.peers, peers);
  $("#kpi-agents").textContent = cnt("online");
  $("#kpi-peers").textContent = peers;
  spark("spark-agents", HIST.agents, "#3fb950"); spark("spark-peers", HIST.peers, "#58a6ff");
  renderOverviewJobs(d.jobs || []);
  if ($("#tab-agents").classList.contains("active")) renderAgentsLive(a);
}
function push(arr, v) { arr.push(v); if (arr.length > 40) arr.shift(); }
function renderOverviewJobs(jobs) {
  const box = $("#overview-jobs");
  if (!jobs.length) { box.innerHTML = '<div class="empty">активных сканов нет</div>'; return; }
  box.innerHTML = jobs.map((j) => {
    const ch = j.chunks || [], done = ch.filter((c) => c.status === "done").length, total = ch.length || 1;
    return `<div class="row"><div class="grow"><b>${esc(j.id)}</b>
      <div class="muted">${done}/${total} чанков · хостов ${(j.stats && j.stats.hosts) || 0} · CVE ${(j.stats && j.stats.cves) || 0}</div>
      <div class="progress"><i style="width:${(done / total * 100).toFixed(0)}%"></i></div></div>
      <span class="pill ${j.status}">${j.status}</span></div>`;
  }).join("");
}

// ── ОБЗОР ───────────────────────────────────────────────────────────────────
async function loadOverview() {
  try {
    const o = await api("/overview");
    push(HIST.cve, o.findings); push(HIST.captured, o.captured);
    $("#kpi-cve").textContent = o.findings; $("#kpi-captured").textContent = o.captured;
    spark("spark-cve", HIST.cve, "#f85149"); spark("spark-captured", HIST.captured, "#a371f7");
  } catch (e) {}
}
setInterval(() => { if (!$("#app").classList.contains("hidden")) loadOverview(); }, 5000);

// ── АГЕНТЫ ──────────────────────────────────────────────────────────────────
TAB_LOADERS.agents = loadAgents;
async function loadAgents() { try { renderAgentsLive(await api("/agents")); } catch (e) {} }
function sparkline(arr) {
  if (!arr || !arr.length) return "";
  return '<span class="spark-row">' + arr.slice(-18).map((v) =>
    `<i style="height:${Math.max(2, Math.min(22, v / 100 * 22))}px"></i>`).join("") + "</span>";
}
function renderAgentsLive(agents) {
  const list = $("#agents-list");
  if (!agents.length) { list.innerHTML = '<div class="empty">нод пока нет</div>'; return; }
  list.innerHTML = agents.map((a) => {
    const roles = (a.roles || []).map((r) => `<span class="pill role">${esc(r)}</span>`).join(" ");
    const isExp = (a.roles || []).includes("exploiter");
    const cpu = a.cpu || (a.live && a.live.cpu);
    return `<div class="row"><span class="pill ${a.status}">${esc(a.status)}</span>
      <div class="grow"><b>${esc(a.name)}</b> <span class="muted">${esc(a.tunnel_ip || "")}</span> ${roles}
        <div class="muted">CPU ${sparkline(cpu)}</div></div>
      ${a.status === "online" ? (isExp
        ? `<button class="mini" onclick="revokeRole('${a.id}')">снять exploiter</button>`
        : `<button class="mini success" onclick="assignRole('${a.id}')">назначить exploiter</button>`) : ""}
      <button class="mini" onclick="updAgentApi('${a.id}')">обновить</button>
      <button class="mini danger" onclick="destroyAgent('${a.id}')">уничтожить</button>
      <button class="mini" onclick="delAgent('${a.id}')">удалить</button></div>`;
  }).join("");
}
$("#ag-add").addEventListener("click", async () => {
  const log = $("#ag-provision-log");
  log.textContent = "провижнинг… (SSH + вброс AWG-ключа, ~10-20с)";
  try {
    const r = await api("/agents", { method: "POST", body: {
      name: $("#ag-name").value || $("#ag-host").value, ssh_host: $("#ag-host").value,
      ssh_port: +$("#ag-port").value, ssh_user: $("#ag-user").value, ssh_password: $("#ag-pass").value } });
    log.textContent = `[${r.status}] ${r.name} · ${r.tunnel_ip}\n` + (r.log || []).join("\n");
    loadAgents();
  } catch (e) { log.textContent = "ошибка: " + e.message; }
});
window.assignRole = async (id) => { const l = $("#ag-provision-log"); l.textContent = "донастройка ноды под эксплуатацию…"; try { await api(`/agents/${id}/role/exploiter`, { method: "POST" }); l.textContent = "роль exploiter назначена (донастройка выполнена)"; } catch (e) { l.textContent = "ошибка: " + e.message; } loadAgents(); };
window.revokeRole = async (id) => { try { await api(`/agents/${id}/role/exploiter`, { method: "DELETE" }); } catch (e) {} loadAgents(); };
window.destroyAgent = async (id) => { if (confirm("Уничтожить ноду (self-destruct)?")) { try { await api(`/agents/${id}/destroy`, { method: "POST" }); } catch (e) {} loadAgents(); } };
window.delAgent = async (id) => { if (confirm("Удалить ноду из реестра?")) { try { await api(`/agents/${id}`, { method: "DELETE" }); } catch (e) {} loadAgents(); } };
window.updAgentApi = async (id) => { try { const r = await api(`/agents/${id}/update`, { method: "POST", body: { transport: "api" } }); alert("Обновление агента: " + JSON.stringify(r.response || r)); } catch (e) { alert("ошибка: " + e.message); } };

// ── ЦЕЛИ ────────────────────────────────────────────────────────────────────
$("#targets-save").addEventListener("click", async () => {
  try {
    const r = await api("/targets", { method: "POST", body: { raw: $("#targets-raw").value } });
    $("#targets-result").textContent = `Канон: ${r.count} целей (IPv4 ${r.v4n} · IPv6 ${r.v6n})\n` +
      (r.notes && r.notes.length ? "Заметки:\n" + r.notes.join("\n") + "\n" : "") + "\n" + r.targets.slice(0, 60).join("\n");
  } catch (e) { $("#targets-result").textContent = "ошибка: " + e.message; }
});

// ── СКАН ────────────────────────────────────────────────────────────────────
$("#sc-start").addEventListener("click", async () => {
  const opts = { ports: { mode: $("#sc-ports-mode").value, value: $("#sc-ports-val").value },
    timing: +$("#sc-timing").value, jobs: +$("#sc-jobs").value,
    no_udp: $("#sc-noudp").checked, no_tcp: $("#sc-notcp").checked,
    skip_disc: $("#sc-pn").checked, no_preflight: $("#sc-nopre").checked };
  try {
    const r = await api("/jobs", { method: "POST", body: { opts, mode: $("#sc-mode").value, diff_against: $("#sc-diff").value || null } });
    $("#scan-result").textContent = "джоба запущена: " + r.job_id + "\nсмотри «Обзор» и «Отчёты» — чанки разъезжаются по агентам";
  } catch (e) { $("#scan-result").textContent = "ошибка: " + e.message; }
});
TAB_LOADERS.scan = async () => { try { const jobs = await api("/jobs"); $("#sc-diff").innerHTML = '<option value="">— нет —</option>' + jobs.map((j) => `<option value="${j.id}">${j.id}</option>`).join(""); } catch (e) {} };

// ── ОТЧЁТЫ ──────────────────────────────────────────────────────────────────
TAB_LOADERS.reports = loadJobs;
async function loadJobs() {
  try {
    const jobs = await api("/jobs");
    $("#jobs-list").innerHTML = jobs.length ? jobs.map((j) => `<div class="row"><span class="pill ${j.status}">${j.status}</span>
      <div class="grow"><b>${esc(j.id)}</b><div class="muted">${j.mode} · хостов ${(j.stats && j.stats.hosts) || 0} · CVE ${(j.stats && j.stats.cves) || 0}</div></div>
      <button class="mini" onclick="viewReport('${j.id}')">отчёт</button>
      <button class="mini" onclick="window.open('/api/jobs/${j.id}/artifact/findings.json','_blank')">json</button>
      ${j.diff_against ? `<button class="mini" onclick="showDiff('${j.diff_against}','${j.id}')">diff</button>` : ""}</div>`).join("")
      : '<div class="empty">джоб нет</div>';
  } catch (e) {}
}
window.viewReport = (id) => {
  $("#report-title").textContent = id;
  $("#report-frame").src = `/api/jobs/${id}/report`;
  $("#report-actions").innerHTML =
    `<button class="mini" onclick="window.open('/api/jobs/${id}/artifact/report.md','_blank')">скачать .md</button>
     <button class="mini" onclick="window.open('/api/jobs/${id}/artifact/findings.csv','_blank')">скачать .csv</button>`;
};
window.showDiff = async (a, b) => {
  try {
    const d = await api(`/diff?a=${a}&b=${b}`);
    $("#diff-card").classList.remove("hidden");
    $("#diff-result").innerHTML = `<div class="muted">${a} → ${b}</div>
      <p>Новые: <b style="color:var(--bad)">${d.counts.added}</b> · Ушедшие: <b style="color:var(--good)">${d.counts.removed}</b> · Остались: <b>${d.counts.kept}</b></p>
      <table><tr><th>Δ</th><th>Хост</th><th>CVE</th><th>CVSS</th></tr>
      ${d.added.map((f) => `<tr><td style="color:var(--bad)">+</td><td>${esc(f.host)}</td><td>${esc(f.cve)}</td><td>${f.cvss}</td></tr>`).join("")}
      ${d.removed.map((f) => `<tr><td style="color:var(--good)">−</td><td>${esc(f.host)}</td><td>${esc(f.cve)}</td><td>${f.cvss}</td></tr>`).join("")}</table>`;
  } catch (e) {}
};

// ── ЭКСПЛУАТАЦИЯ ────────────────────────────────────────────────────────────
TAB_LOADERS.exploit = loadExploit;
async function loadExploit() {
  try {
    const jobs = await api("/jobs");
    $("#ex-job").innerHTML = jobs.map((j) => `<option value="${j.id}">${j.id}</option>`).join("");
    const exp = await api("/exploiters");
    $("#ex-agent").innerHTML = exp.length ? exp.map((a) => `<option value="${a.id}">${esc(a.name)} (${a.tunnel_ip})</option>`).join("") : '<option value="">нет нод с ролью exploiter</option>';
    await loadExploitList(); await loadCaptures();
  } catch (e) {}
}
$("#ex-refresh").addEventListener("click", loadExploit);
$("#ex-job").addEventListener("change", loadExploitList);
async function loadExploitList() {
  const jid = $("#ex-job").value;
  if (!jid) { $("#exploit-list").innerHTML = '<div class="empty">выбери джобу</div>'; return; }
  try {
    const rows = await api(`/jobs/${jid}/exploitable`);
    $("#exploit-list").innerHTML = rows.length ? `<table>
      <tr><th>Хост</th><th>CVE</th><th>Модуль</th><th>Порт</th><th>Проверка</th><th>Закрепление</th></tr>
      ${rows.map((r, i) => `<tr id="ex-${i}"><td>${esc(r.host)}</td>
        <td>${esc(r.cve)} <span class="badge ${r.severity}">${r.severity}</span></td>
        <td>${esc(r.module.name)}</td><td>${r.port}</td>
        <td><button class="mini" onclick="exCheck(${i},'${esc(r.host)}','${esc(r.cve)}',${r.port})">check</button> <span class="ex-verdict"></span></td>
        <td><label class="confirm"><input type="checkbox" class="ex-confirm"> подтверждаю</label>
        <button class="mini danger" onclick="exCapture(${i},'${esc(r.host)}','${esc(r.cve)}',${r.port})">закрепиться</button></td></tr>`).join("")}</table>`
      : '<div class="empty">под находки этой джобы нет модулей эксплуатации</div>';
  } catch (e) { $("#exploit-list").innerHTML = '<div class="empty">ошибка: ' + esc(e.message) + '</div>'; }
}
window.exCheck = async (i, host, cve, port) => {
  const cell = $(`#ex-${i} .ex-verdict`); cell.textContent = "…";
  try {
    const r = await api("/exploit/check", { method: "POST", body: { agent_id: $("#ex-agent").value, host, cve, port } });
    cell.innerHTML = r.exploitable ? `<span style="color:var(--good)">✓ уязвим</span>` : `<span class="muted">✗ ${esc(r.evidence || r.error || "")}</span>`;
  } catch (e) { cell.innerHTML = `<span style="color:var(--bad)">ошибка</span>`; }
};
window.exCapture = async (i, host, cve, port) => {
  if (!$(`#ex-${i} .ex-confirm`).checked) { alert("Отметь «подтверждаю» — закрепление только с явным подтверждением."); return; }
  const agent = $("#ex-agent").value;
  if (!agent) { alert("Нет ноды с ролью exploiter — назначь во вкладке «Агенты»."); return; }
  try {
    const r = await api("/exploit/capture", { method: "POST", body: { agent_id: agent, host, cve, port, confirm: true } });
    if (r.success) alert(`ТОЧКА ЗАХВАЧЕНА\nфлаг: ${r.flag || "—"}\nмаркер: ${r.marker}`);
    else alert("Не удалось: " + (r.error || "см. лог"));
  } catch (e) { alert("ошибка: " + e.message); }
  loadCaptures(); loadOverview();
};
async function loadCaptures() {
  try {
    const caps = await api("/captures");
    $("#captures-list").innerHTML = caps.length ? `<table><tr><th>Время</th><th>Цель</th><th>CVE</th><th>Фаза</th><th>Итог</th><th>Флаг</th></tr>
      ${caps.map((c) => `<tr><td>${new Date(c.ts * 1000).toLocaleTimeString("ru-RU")}</td><td>${esc(c.target)}</td><td>${esc(c.cve)}</td><td>${esc(c.phase)}</td>
      <td>${c.success ? '<span style="color:var(--good)">✓</span>' : '<span style="color:var(--bad)">✗</span>'}</td><td>${esc(c.flag || "")}</td></tr>`).join("")}</table>`
      : '<div class="empty">захватов пока нет</div>';
  } catch (e) {}
}

// ── VPN ─────────────────────────────────────────────────────────────────────
TAB_LOADERS.vpn = async () => {
  try {
    const v = await api("/vpn");
    $("#vpn-status").innerHTML = `<div class="stat-list">
      <div class="kv"><span>Интерфейс</span><b>${esc(v.iface || "awg0")} ${v.up ? "↑" : "↓"}</b></div>
      <div class="kv"><span>Порт</span><b>${v.listen_port || "—"}</b></div>
      <div class="kv"><span>Пиров</span><b>${v.peer_count || 0}</b></div></div>
      ${(v.peers || []).map((p) => `<div class="row"><span class="dot ok"></span><div class="grow"><b>${esc(p.peer.slice(0, 24))}…</b><div class="muted">handshake: ${esc(p.handshake || "нет")}</div></div></div>`).join("")}`;
  } catch (e) {}
};

// ── БЭКАПЫ ──────────────────────────────────────────────────────────────────
TAB_LOADERS.backups = loadBackups;
async function loadBackups() {
  try {
    const list = await api("/backups");
    $("#backups-list").innerHTML = list.length ? list.map((b) => `<div class="row"><span data-ic="save"></span>
      <div class="grow"><b>${esc(b.name)}</b> <span class="muted">${(b.size / 1024).toFixed(0)} KB</span></div>
      <button class="mini" onclick="restoreBackup('${b.name}')">восстановить</button></div>`).join("") : '<div class="empty">бэкапов нет</div>';
    GWFX && GWFX.icons($("#backups-list"));
  } catch (e) {}
}
$("#bk-create").addEventListener("click", async () => { try { await api("/backups", { method: "POST" }); } catch (e) {} loadBackups(); });
window.restoreBackup = async (n) => { if (confirm("Восстановить из бэкапа?")) { try { const r = await api(`/backups/${n}/restore`, { method: "POST" }); alert("Восстановлено строк: " + r.restored_rows); } catch (e) { alert("ошибка: " + e.message); } } };

// ── ОБНОВЛЕНИЯ ──────────────────────────────────────────────────────────────
TAB_LOADERS.updates = async () => {
  try {
    const agents = await api("/agents");
    $("#up-agents").innerHTML = agents.length ? agents.map((a) => `<div class="row"><div class="grow"><b>${esc(a.name)}</b> <span class="muted">${esc(a.tunnel_ip || "")}</span></div>
      <button class="mini" onclick="updAgentApi('${a.id}')">обновить (API)</button></div>`).join("") : '<div class="empty">нод нет</div>';
  } catch (e) {}
};
$("#up-host-git").addEventListener("click", async () => {
  $("#up-host-log").textContent = "обновление хоста (git pull)…";
  try { const r = await api("/update/host", { method: "POST", body: { method: "git" } }); $("#up-host-log").textContent = (r.log || []).join("\n") + "\n" + (r.note || ""); }
  catch (e) { $("#up-host-log").textContent = "ошибка: " + e.message; }
});

// ── НАСТРОЙКИ ───────────────────────────────────────────────────────────────
TAB_LOADERS.settings = loadUsers;
$("#set-save").addEventListener("click", async () => {
  try {
    const r = await api("/settings/credentials", { method: "POST", body: { login: $("#set-login").value, password: $("#set-pass").value } });
    $("#set-result").textContent = "сохранено. Новый логин: " + (r.login || "") + " — перелогинься.";
  } catch (e) { $("#set-result").textContent = "ошибка: " + e.message; }
});
$("#usr-add").addEventListener("click", async () => {
  try { await api("/users", { method: "POST", body: { login: $("#usr-login").value, password: $("#usr-pass").value } }); $("#usr-login").value = ""; $("#usr-pass").value = ""; loadUsers(); }
  catch (e) { alert("ошибка: " + e.message); }
});
async function loadUsers() {
  try {
    const users = await api("/users");
    $("#users-list").innerHTML = users.map((u) => `<div class="row"><span data-ic="user"></span><div class="grow"><b>${esc(u.login)}</b></div>
      <button class="mini danger" onclick="delUser('${esc(u.login)}')">удалить</button></div>`).join("") || '<div class="empty">нет</div>';
    GWFX && GWFX.icons($("#users-list"));
  } catch (e) {}
}
window.delUser = async (login) => { if (confirm(`Удалить ${login}?`)) { try { await api(`/users/${encodeURIComponent(login)}`, { method: "DELETE" }); } catch (e) { alert(e.message); } loadUsers(); } };

// ── старт ───────────────────────────────────────────────────────────────────
initFx(); initLoginLogo();
(async () => { try { const m = await api("/me"); m.authenticated ? showApp() : showLogin(); } catch (e) { showLogin(); } })();
