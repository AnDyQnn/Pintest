// app.js — логика дашборда: авторизация, вкладки, живой статус, все действия.
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

// ------------------------------ авторизация --------------------------------
function showLogin() { $("#login").classList.remove("hidden"); $("#app").classList.add("hidden"); }
function showApp() { $("#login").classList.add("hidden"); $("#app").classList.remove("hidden"); startLive(); loadOverview(); }

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/login", { method: "POST", body: { user: $("#login-user").value, password: $("#login-pass").value } });
    showApp();
  } catch (err) { $("#login-err").textContent = err.message; }
});
$("#logout").addEventListener("click", async () => { await api("/logout", { method: "POST" }); showLogin(); });

// ------------------------------ вкладки ------------------------------------
const TAB_LOADERS = {};
$$(".tab").forEach((t) => t.addEventListener("click", () => {
  $$(".tab").forEach((x) => x.classList.remove("active"));
  $$(".panel").forEach((x) => x.classList.remove("active"));
  t.classList.add("active");
  $("#tab-" + t.dataset.tab).classList.add("active");
  if (TAB_LOADERS[t.dataset.tab]) TAB_LOADERS[t.dataset.tab]();
}));

// ------------------------------ живой статус (WS) --------------------------
let ws = null, lastLive = { agents: [] };
function startLive() {
  if (ws) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/api/live`);
  ws.onmessage = (ev) => { lastLive = JSON.parse(ev.data); renderLive(lastLive); };
  ws.onclose = () => { ws = null; setTimeout(startLive, 2000); };
}
function renderLive(d) {
  renderTopology($("#topology"), d);
  const a = d.agents || [];
  const cnt = (s) => a.filter((x) => x.status === s).length;
  $("#topo-legend").textContent = `· на связи ${cnt("online")} · потеряно ${cnt("lost")} · уничтожено ${cnt("destroyed")}`;
  $("#top-stats").innerHTML =
    `<span class="st">Ноды: <b>${a.length}</b></span>
     <span class="st">Онлайн: <b>${cnt("online")}</b></span>
     <span class="st">VPN пиров: <b>${(d.vpn && d.vpn.peer_count) || 0}</b></span>
     <span class="st">Активных джоб: <b>${(d.jobs || []).length}</b></span>`;
  renderOverviewJobs(d.jobs || []);
  if ($("#tab-agents").classList.contains("active")) renderAgentsLive(a);
}

// ------------------------------ ОБЗОР --------------------------------------
async function loadOverview() {
  try {
    const o = await api("/overview");
    $("#ver").textContent = "v" + o.version;
    $("#overview-stats").innerHTML = `
      <div class="kv"><span>Агентов всего</span><b>${o.agents.total}</b></div>
      <div class="kv"><span>Онлайн</span><b>${o.agents.by_status.online || 0}</b></div>
      <div class="kv"><span>Джоб</span><b>${o.jobs.total}</b></div>
      <div class="kv"><span>Находок (CVE)</span><b>${o.findings}</b></div>
      <div class="kv"><span>Захвачено точек</span><b>${o.captured}</b></div>
      <div class="kv"><span>VPN</span><b>${o.vpn.up ? "поднят" : "нет"}</b></div>`;
  } catch (e) {}
}
function renderOverviewJobs(jobs) {
  $("#overview-jobs").innerHTML = jobs.length ? jobs.map((j) => {
    const done = (j.chunks || []).filter((c) => c.status === "done").length;
    const total = (j.chunks || []).length || 1;
    return `<div class="row"><div class="grow"><b>${esc(j.id)}</b>
      <div class="muted">${done}/${total} чанков · хостов ${(j.stats && j.stats.hosts) || 0} · CVE ${(j.stats && j.stats.cves) || 0}</div>
      <div class="progress"><i style="width:${(done / total * 100).toFixed(0)}%"></i></div></div>
      <span class="pill ${j.status}">${j.status}</span></div>`;
  }).join("") : '<div class="muted">активных джоб нет</div>';
}

// ------------------------------ АГЕНТЫ -------------------------------------
TAB_LOADERS.agents = loadAgents;
async function loadAgents() { renderAgentsLive(lastLive.agents || (await api("/agents"))); }
function sparkline(arr) {
  if (!arr || !arr.length) return "";
  return '<span class="spark">' + arr.slice(-20).map((v) =>
    `<i style="height:${Math.max(2, Math.min(22, v / 100 * 22))}px"></i>`).join("") + "</span>";
}
async function renderAgentsLive(agents) {
  const list = $("#agents-list");
  list.innerHTML = agents.map((a) => {
    const roles = (a.roles || []).map((r) => `<span class="pill role">${esc(r)}</span>`).join(" ");
    const isExp = (a.roles || []).includes("exploiter");
    const cpu = a.cpu || (a.live && a.live.cpu);
    return `<div class="row">
      <span class="pill ${a.status}">${esc(a.status)}</span>
      <div class="grow"><b>${esc(a.name)}</b> <span class="muted">${esc(a.tunnel_ip || "")}</span> ${roles}
        <div class="muted">CPU ${sparkline(cpu)}</div></div>
      ${a.status === "online" ? (isExp
        ? `<button class="small ghost" onclick="revokeRole('${a.id}')">снять exploiter</button>`
        : `<button class="small" onclick="assignRole('${a.id}')">назначить exploiter</button>`) : ""}
      <button class="small ghost" onclick="updAgentApi('${a.id}')">обновить</button>
      <button class="small danger" onclick="destroyAgent('${a.id}')">уничтожить</button>
      <button class="small ghost" onclick="delAgent('${a.id}')">удалить</button>
    </div>`;
  }).join("") || '<div class="muted">нод пока нет</div>';
}
$("#ag-add").addEventListener("click", async () => {
  const log = $("#ag-provision-log");
  log.textContent = "провижнинг… (SSH + вброс AWG-ключа)";
  try {
    const r = await api("/agents", { method: "POST", body: {
      name: $("#ag-name").value, ssh_host: $("#ag-host").value,
      ssh_port: +$("#ag-port").value, ssh_user: $("#ag-user").value,
      ssh_password: $("#ag-pass").value } });
    log.textContent = `[${r.status}] ${r.name} · ${r.tunnel_ip}\n` + (r.log || []).join("\n");
    loadAgents();
  } catch (e) { log.textContent = "ошибка: " + e.message; }
});
window.assignRole = async (id) => { await api(`/agents/${id}/role/exploiter`, { method: "POST" }); loadAgents(); };
window.revokeRole = async (id) => { await api(`/agents/${id}/role/exploiter`, { method: "DELETE" }); loadAgents(); };
window.destroyAgent = async (id) => { if (confirm("Уничтожить ноду (self-destruct)?")) { await api(`/agents/${id}/destroy`, { method: "POST" }); loadAgents(); } };
window.delAgent = async (id) => { if (confirm("Удалить ноду из реестра?")) { await api(`/agents/${id}`, { method: "DELETE" }); loadAgents(); } };
window.updAgentApi = async (id) => { const r = await api(`/agents/${id}/update`, { method: "POST", body: { transport: "api" } }); alert("Обновление агента: " + JSON.stringify(r.response || r)); };

// ------------------------------ ЦЕЛИ ---------------------------------------
$("#targets-save").addEventListener("click", async () => {
  try {
    const r = await api("/targets", { method: "POST", body: { raw: $("#targets-raw").value } });
    $("#targets-result").textContent =
      `Канон: ${r.count} целей (IPv4 ${r.v4n} · IPv6 ${r.v6n})\n` +
      (r.notes && r.notes.length ? "Заметки:\n" + r.notes.join("\n") : "мусора не найдено") +
      "\n\n" + r.targets.slice(0, 50).join("\n");
  } catch (e) { $("#targets-result").textContent = "ошибка: " + e.message; }
});

// ------------------------------ СКАН ---------------------------------------
$("#sc-start").addEventListener("click", async () => {
  const opts = {
    ports: { mode: $("#sc-ports-mode").value, value: $("#sc-ports-val").value },
    timing: +$("#sc-timing").value, jobs: +$("#sc-jobs").value,
    no_udp: $("#sc-noudp").checked, no_tcp: $("#sc-notcp").checked,
    skip_disc: $("#sc-pn").checked, no_preflight: $("#sc-nopre").checked,
  };
  try {
    const r = await api("/jobs", { method: "POST", body: {
      opts, mode: $("#sc-mode").value, diff_against: $("#sc-diff").value || null } });
    $("#scan-result").textContent = "джоба запущена: " + r.job_id;
  } catch (e) { $("#scan-result").textContent = "ошибка: " + e.message; }
});
TAB_LOADERS.scan = async () => {
  const jobs = await api("/jobs");
  $("#sc-diff").innerHTML = '<option value="">— нет —</option>' +
    jobs.map((j) => `<option value="${j.id}">${j.id}</option>`).join("");
};

// ------------------------------ ОТЧЁТЫ -------------------------------------
TAB_LOADERS.reports = loadJobs;
async function loadJobs() {
  const jobs = await api("/jobs");
  $("#jobs-list").innerHTML = jobs.map((j) => `<div class="row">
    <span class="pill ${j.status}">${j.status}</span>
    <div class="grow"><b>${esc(j.id)}</b>
      <div class="muted">${j.mode} · хостов ${(j.stats && j.stats.hosts) || 0} · CVE ${(j.stats && j.stats.cves) || 0}</div></div>
    <button class="small" onclick="viewReport('${j.id}')">отчёт</button>
    <a class="small" href="/api/jobs/${j.id}/artifact/findings.json" target="_blank"><button class="small ghost">json</button></a>
    ${j.diff_against ? `<button class="small ghost" onclick="showDiff('${j.diff_against}','${j.id}')">diff</button>` : ""}
  </div>`).join("") || '<div class="muted">джоб нет</div>';
}
window.viewReport = (id) => {
  $("#report-title").textContent = id;
  $("#report-frame").src = `/api/jobs/${id}/report`;
  $("#report-actions").innerHTML =
    `<a href="/api/jobs/${id}/artifact/report.md" target="_blank"><button class="small ghost">скачать .md</button></a>
     <a href="/api/jobs/${id}/artifact/findings.csv" target="_blank"><button class="small ghost">скачать .csv</button></a>`;
};
window.showDiff = async (a, b) => {
  const d = await api(`/diff?a=${a}&b=${b}`);
  $("#diff-card").classList.remove("hidden");
  $("#diff-result").innerHTML = `<div class="muted">${a} → ${b}</div>
    <p>Новые: <b style="color:var(--bad)">${d.counts.added}</b> ·
       Ушедшие: <b style="color:var(--ok)">${d.counts.removed}</b> ·
       Остались: <b>${d.counts.kept}</b></p>
    <table><tr><th>Δ</th><th>Хост</th><th>CVE</th><th>CVSS</th></tr>
    ${d.added.map((f) => `<tr><td style="color:var(--bad)">+</td><td>${esc(f.host)}</td><td>${esc(f.cve)}</td><td>${f.cvss}</td></tr>`).join("")}
    ${d.removed.map((f) => `<tr><td style="color:var(--ok)">−</td><td>${esc(f.host)}</td><td>${esc(f.cve)}</td><td>${f.cvss}</td></tr>`).join("")}
    </table>`;
};

// ------------------------------ ЭКСПЛУАТАЦИЯ -------------------------------
TAB_LOADERS.exploit = loadExploit;
async function loadExploit() {
  const jobs = await api("/jobs");
  $("#ex-job").innerHTML = jobs.map((j) => `<option value="${j.id}">${j.id}</option>`).join("");
  const exp = await api("/exploiters");
  $("#ex-agent").innerHTML = exp.length
    ? exp.map((a) => `<option value="${a.id}">${esc(a.name)} (${a.tunnel_ip})</option>`).join("")
    : '<option value="">нет нод с ролью exploiter</option>';
  await loadExploitList();
  await loadCaptures();
}
$("#ex-refresh").addEventListener("click", loadExploit);
$("#ex-job").addEventListener("change", loadExploitList);
async function loadExploitList() {
  const jid = $("#ex-job").value;
  if (!jid) { $("#exploit-list").innerHTML = '<div class="muted">нет джобы</div>'; return; }
  const rows = await api(`/jobs/${jid}/exploitable`);
  $("#exploit-list").innerHTML = rows.length ? `<table>
    <tr><th>Хост</th><th>CVE</th><th>Модуль</th><th>Порт</th><th>Проверка</th><th>Закрепление</th></tr>
    ${rows.map((r, i) => `<tr id="ex-${i}">
      <td>${esc(r.host)}</td>
      <td>${esc(r.cve)} <span class="badge ${r.severity}">${r.severity}</span></td>
      <td>${esc(r.module.name)}</td><td>${r.port}</td>
      <td><button class="small ghost" onclick="exCheck(${i},'${esc(r.host)}','${esc(r.cve)}',${r.port})">check</button>
          <span class="ex-verdict"></span></td>
      <td><label class="confirm"><input type="checkbox" class="ex-confirm"> подтверждаю</label>
          <button class="small danger" onclick="exCapture(${i},'${esc(r.host)}','${esc(r.cve)}',${r.port})">закрепиться</button></td>
    </tr>`).join("")}</table>`
    : '<div class="muted">под находки этой джобы нет модулей эксплуатации</div>';
}
window.exCheck = async (i, host, cve, port) => {
  const cell = $(`#ex-${i} .ex-verdict`);
  cell.textContent = "…";
  const r = await api("/exploit/check", { method: "POST", body: { agent_id: $("#ex-agent").value, host, cve, port } });
  cell.innerHTML = r.exploitable
    ? `<span style="color:var(--ok)">✓ уязвим</span>`
    : `<span style="color:var(--muted)">✗ нет (${esc(r.evidence || r.error || "")})</span>`;
};
window.exCapture = async (i, host, cve, port) => {
  const confirmed = $(`#ex-${i} .ex-confirm`).checked;
  if (!confirmed) { alert("Отметь «подтверждаю» — закрепление только с явным подтверждением."); return; }
  const agent = $("#ex-agent").value;
  if (!agent) { alert("Нет ноды с ролью exploiter."); return; }
  const r = await api("/exploit/capture", { method: "POST", body: { agent_id: agent, host, cve, port, confirm: true } });
  if (r.success) alert(`ТОЧКА ЗАХВАЧЕНА\nфлаг: ${r.flag || "—"}\nмаркер: ${r.marker}`);
  else alert("Не удалось: " + (r.error || "см. лог"));
  loadCaptures();
};
async function loadCaptures() {
  const caps = await api("/captures");
  $("#captures-list").innerHTML = caps.length ? `<table>
    <tr><th>Время</th><th>Цель</th><th>CVE</th><th>Фаза</th><th>Итог</th><th>Флаг</th></tr>
    ${caps.map((c) => `<tr><td>${new Date(c.ts * 1000).toLocaleTimeString()}</td>
      <td>${esc(c.target)}</td><td>${esc(c.cve)}</td><td>${esc(c.phase)}</td>
      <td>${c.success ? '<span style="color:var(--ok)">✓</span>' : '<span style="color:var(--bad)">✗</span>'}</td>
      <td>${esc(c.flag || "")}</td></tr>`).join("")}</table>`
    : '<div class="muted">захватов пока нет</div>';
}

// ------------------------------ VPN ----------------------------------------
TAB_LOADERS.vpn = async () => {
  const v = await api("/vpn");
  $("#vpn-status").innerHTML = `<div class="stat-list">
    <div class="kv"><span>Интерфейс</span><b>${esc(v.iface || "awg0")} ${v.up ? "↑" : "↓"}</b></div>
    <div class="kv"><span>Порт</span><b>${v.listen_port || "—"}</b></div>
    <div class="kv"><span>Пиров</span><b>${v.peer_count || 0}</b></div></div>
    ${(v.peers || []).map((p) => `<div class="row"><div class="grow"><b>${esc(p.peer.slice(0, 20))}…</b>
      <div class="muted">handshake: ${esc(p.handshake || "нет")}</div></div></div>`).join("")}`;
};

// ------------------------------ БЭКАПЫ -------------------------------------
TAB_LOADERS.backups = loadBackups;
async function loadBackups() {
  const list = await api("/backups");
  $("#backups-list").innerHTML = list.map((b) => `<div class="row">
    <div class="grow"><b>${esc(b.name)}</b> <span class="muted">${(b.size / 1024).toFixed(0)} KB</span></div>
    <button class="small ghost" onclick="restoreBackup('${b.name}')">восстановить</button></div>`).join("")
    || '<div class="muted">бэкапов нет</div>';
}
$("#bk-create").addEventListener("click", async () => { await api("/backups", { method: "POST" }); loadBackups(); });
window.restoreBackup = async (n) => { if (confirm("Восстановить из бэкапа?")) { const r = await api(`/backups/${n}/restore`, { method: "POST" }); alert("Восстановлено строк: " + r.restored_rows); } };

// ------------------------------ ОБНОВЛЕНИЯ ---------------------------------
TAB_LOADERS.updates = async () => {
  const agents = lastLive.agents || (await api("/agents"));
  $("#up-agents").innerHTML = agents.map((a) => `<div class="row">
    <div class="grow"><b>${esc(a.name)}</b> <span class="muted">${esc(a.tunnel_ip || "")}</span></div>
    <button class="small" onclick="updAgentApi('${a.id}')">обновить (API)</button></div>`).join("")
    || '<div class="muted">нод нет</div>';
};
$("#up-host-git").addEventListener("click", async () => {
  $("#up-host-log").textContent = "обновление хоста (git pull)…";
  const r = await api("/update/host", { method: "POST", body: { method: "git" } });
  $("#up-host-log").textContent = (r.log || []).join("\n") + "\n" + (r.note || "");
});

// ------------------------------ старт --------------------------------------
(async () => {
  try { const m = await api("/me"); m.authenticated ? showApp() : showLogin(); }
  catch (e) { showLogin(); }
})();
