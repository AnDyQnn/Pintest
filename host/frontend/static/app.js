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

// ── анимированный фавикон: радар-развёртка со засветкой узлов сети (в тему сканера) ──
function animatedFavicon() {
  let link = document.querySelector("link[rel='icon']");
  if (!link) { link = document.createElement("link"); link.rel = "icon"; document.head.appendChild(link); }
  const S = 64, cv = document.createElement("canvas"); cv.width = S; cv.height = S;
  const c = cv.getContext("2d"), cx = S / 2, cy = S / 2, R = 25;
  const AC = "#a371f7", AB = "#58a6ff";
  const blips = [
    { a: -1.2, r: 15, col: AB }, { a: 0.5, r: 22, col: "#3fb950" },
    { a: 2.2, r: 12, col: AC }, { a: 3.6, r: 20, col: "#f0883e" }, { a: 5.0, r: 17, col: AB },
  ];
  const rr = (x, y, w, h, rad) => { c.beginPath(); c.moveTo(x + rad, y);
    c.arcTo(x + w, y, x + w, y + h, rad); c.arcTo(x + w, y + h, x, y + h, rad);
    c.arcTo(x, y + h, x, y, rad); c.arcTo(x, y, x + w, y, rad); c.closePath(); };
  function frame(t) {
    c.clearRect(0, 0, S, S);
    rr(1, 1, S - 2, S - 2, 14); c.fillStyle = "#0b0e13"; c.fill();
    c.lineWidth = 1;
    for (let i = 1; i <= 3; i++) { c.beginPath(); c.arc(cx, cy, R * i / 3, 0, 7); c.strokeStyle = "rgba(88,166,255,.16)"; c.stroke(); }
    c.beginPath(); c.moveTo(cx - R, cy); c.lineTo(cx + R, cy); c.moveTo(cx, cy - R); c.lineTo(cx, cy + R); c.strokeStyle = "rgba(88,166,255,.10)"; c.stroke();
    const ang = t * Math.PI * 2;
    for (let k = 0; k < 26; k++) {                 // хвост-развёртка
      const aa = ang - k * 0.06;
      c.beginPath(); c.moveTo(cx, cy); c.lineTo(cx + Math.cos(aa) * R, cy + Math.sin(aa) * R);
      c.strokeStyle = "rgba(163,113,247," + ((1 - k / 26) * 0.5) + ")"; c.lineWidth = 1.6; c.stroke();
    }
    c.beginPath(); c.moveTo(cx, cy); c.lineTo(cx + Math.cos(ang) * R, cy + Math.sin(ang) * R); c.strokeStyle = AC; c.lineWidth = 1.7; c.stroke();
    blips.forEach((b) => {                          // узлы «загораются» по проходу луча
      let da = (ang - b.a) % (Math.PI * 2); if (da < 0) da += Math.PI * 2;
      const lit = Math.max(0, 1 - da / 1.3);
      const bx = cx + Math.cos(b.a) * b.r, by = cy + Math.sin(b.a) * b.r;
      c.globalAlpha = 0.3 + lit * 0.7; c.fillStyle = b.col;
      if (lit > 0.25) { c.shadowColor = b.col; c.shadowBlur = 7 * lit; }
      c.beginPath(); c.arc(bx, by, 1.7 + lit * 1.8, 0, 7); c.fill();
      c.shadowBlur = 0; c.globalAlpha = 1;
    });
    c.beginPath(); c.arc(cx, cy, 2.6, 0, 7); c.fillStyle = AB; c.fill();
  }
  const N = 48, DUR = 2600, frames = [];
  for (let i = 0; i < N; i++) { frame(i / N); frames.push(cv.toDataURL("image/png")); }
  let start = performance.now(), last = -1;
  (function tick(ts) { const idx = Math.floor(((ts - start) % DUR) / DUR * N) % N; if (idx !== last) { last = idx; link.href = frames[idx]; } requestAnimationFrame(tick); })(start);
}

// ── графика (fx.js) ────────────────────────────────────────────────────────
function initFx() {
  animatedFavicon();
  if (!window.GWFX) return;
  GWFX.background(document.getElementById("fx-bg"), "hi");
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
function showApp() { $("#login").classList.add("hidden"); $("#app").classList.remove("hidden"); GWFX && GWFX.icons(document); startLive(); loadOverview(); loadVersion(); }
let APP_VERSION = "";
async function loadVersion() {
  try { const h = await api("/health"); APP_VERSION = h.version || ""; const el = $("#app-version"); if (el) el.textContent = APP_VERSION ? "v" + APP_VERSION : ""; return APP_VERSION; }
  catch (e) { return APP_VERSION; }
}
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
  const topo = d.topology || {};
  const tc = topo.counts || {};
  const tt = (tc.captured||0)+(tc.exploitable||0)+(tc.vulnerable||0)+(tc.discovered||0)+(tc.pending||0);
  const reroute = topo.rerouted ? ` · рокировок ${topo.rerouted}` : "";
  const cut = topo.unreachable ? ` · недостижимо ${topo.unreachable}` : "";
  $("#topo-legend").textContent =
    `· ноды: на связи ${cnt("online")} · потеряно ${cnt("lost")} · уничтожено ${cnt("destroyed")}` +
    (tt ? `  ·  цели: обработано ${tc.captured||0} · обрабатываемо ${tc.exploitable||0} · уязвимо ${tc.vulnerable||0} · обнаружено ${tc.discovered||0} · в очереди ${tc.pending||0}${reroute}${cut}` : "");
  const peers = (d.vpn && (d.vpn.active != null ? d.vpn.active : d.vpn.peer_count)) || 0;  // АКТИВНЫЕ сессии (свежий handshake), не сумма конфигов
  const total = a.length, reach = topo.reachable || 0, unreach = topo.unreachable || 0;
  $("#kpi-agents").textContent = cnt("online");
  $("#kpi-agents-total").textContent = total ? "/" + total : "";
  $("#kpi-reach").textContent = reach;
  $("#kpi-reach-total").textContent = (reach + unreach) ? "/" + (reach + unreach) : "";
  $("#kpi-reroute").textContent = topo.rerouted || 0;
  $("#kpi-peers").textContent = peers;
  renderReach(topo, a);
  renderOverviewJobs(d.jobs || []);
  if ($("#tab-agents").classList.contains("active")) renderAgentsLive(a);
}
function push(arr, v) { arr.push(v); if (arr.length > 40) arr.shift(); }
function renderReach(topo, agents) {
  const box = $("#reach-panel"); if (!box) return;
  const name = {}; (agents || []).forEach((x) => { name[x.id] = x.name; });
  const reach = topo.reachable || 0, unreach = topo.unreachable || 0, sp = topo.single_points || {};
  if (!reach && !unreach) { box.innerHTML = '<div class="empty">запусти скан — появятся цели</div>'; return; }
  const relays = topo.relays || [];
  let html = `<div class="reach-line"><span class="dot ok"></span><div class="grow">Достижимо целей (есть живой маршрут)</div><b>${reach}</b></div>`;
  html += `<div class="reach-line"><span class="dot ${unreach ? "bad" : "ok"}"></span><div class="grow">Недостижимо (маршрут отрезан)</div><b>${unreach}</b></div>`;
  if (relays.length)
    html += `<div class="reach-line"><span class="dot" style="background:#2dd4bf;box-shadow:0 0 7px #2dd4bf"></span><div class="grow">Плацдармов-реле (держат сеть при падении агентов)</div><b>${relays.length}</b></div>`;
  const keys = Object.keys(sp);
  html += keys.length
    ? keys.map((id) => `<div class="spf">если выпадет <b>${esc(name[id] || id.slice(0, 8))}</b> — теряешь доступ к: ${sp[id].map(esc).join(", ")}</div>`).join("")
    : '<div class="muted" style="margin-top:.6rem">единых точек отказа нет — сеть самовосстановится (рокировка на живого агента или реле через захваченный узел)</div>';
  box.innerHTML = html;
}
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
    $("#kpi-cve").textContent = o.findings;
    $("#kpi-captured").textContent = o.captured;
  } catch (e) {}
}
setInterval(() => { if (!$("#app").classList.contains("hidden")) loadOverview(); }, 5000);

// ── АГЕНТЫ ──────────────────────────────────────────────────────────────────
TAB_LOADERS.agents = loadAgents;
async function loadAgents() { try { renderAgentsLive(await api("/agents")); } catch (e) {} prefillAgentPort(); }
// Порт новой ноды «подсасывается» сам: последний введённый (localStorage) → SSH-порт
// хоста (обычно все серверы на одном порту) → 22. Поле не трогаем, если юзер уже вписал.
function prefillAgentPort() {
  // подставляем ТОЛЬКО последний введённый порт (память); иначе поле пустое —
  // placeholder показывает 22 (дефолтный SSH). Порт ХОСТА тут ни при чём:
  // это порт SSH самого агент-сервера, куда хост заходит по SSH.
  const el = $("#ag-port"); if (!el || el.value.trim()) return;
  try { const p = localStorage.getItem("pintest_agent_port") || ""; if (p) el.value = p; } catch (e) {}
}
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
  const deploy = $("#ag-deploy") ? $("#ag-deploy").checked : false;
  log.textContent = deploy ? "полный деплой с хоста… (apt + сборка + туннель, до нескольких минут)" : "провижнинг… (SSH + вброс AWG-ключа, ~10-20с)";
  try {
    const port = +$("#ag-port").value || 22;
    const r = await api("/agents", { method: "POST", body: {
      name: $("#ag-name").value || $("#ag-host").value, ssh_host: $("#ag-host").value,
      ssh_port: port, ssh_user: $("#ag-user").value, ssh_password: $("#ag-pass").value,
      full_deploy: deploy } });
    try { localStorage.setItem("pintest_agent_port", String(port)); } catch (e) {}  // запомнить порт
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
// ── СКАН: живая оценка нагрузки/шума (по железу слабейшего онлайн-агента) ──
function scanCaps() {
  const ags = (lastLive.agents || []).filter((a) => a.status === "online");
  let mem = Infinity, cpu = Infinity;
  ags.forEach((a) => { const c = a.caps || {};
    if (c.mem_total_mb) mem = Math.min(mem, c.mem_total_mb);
    if (c.cpu_count) cpu = Math.min(cpu, c.cpu_count); });
  return { mem: isFinite(mem) ? mem : null, cpu: isFinite(cpu) ? cpu : null, agents: ags.length };
}
let SCAN_BLOCKED = false;
function scanAdvice() {
  const el = $("#scan-advice"); if (!el) return;
  const pmode = $("#sc-ports-mode").value, pval = +$("#sc-ports-val").value || 0;
  const timing = +$("#sc-timing").value, jobs = +$("#sc-jobs").value || 1;
  const nopre = $("#sc-nopre") && $("#sc-nopre").checked;
  const nports = pmode === "all" ? 65535 : (pmode === "top" ? pval : 200);
  const caps = scanCaps();
  const PER = 220;                       // МБ на один поток nmap с NSE
  const need = jobs * PER;
  const msgs = []; let level = "ok";
  const bump = (lv) => { const o = { ok: 0, warn: 1, block: 2 }; if (o[lv] > o[level]) level = lv; };
  if (timing >= 5) { msgs.push("⚡ -T5 (insane) — очень шумно, IDS/цель заметят и могут резать"); bump("warn"); }
  if (nports >= 65535) { msgs.push("📡 все 65535 портов — долго и шумно; для CTF хватает топ-1000"); bump("warn"); }
  if (nopre) { msgs.push("🔊 --no-preflight — без мягкой разведки, агрессивнее"); bump("warn"); }
  if (caps.mem) {
    if (need > caps.mem * 0.85) { msgs.push("💥 -j " + jobs + " × ~" + PER + "МБ ≈ " + need + "МБ, а у слабейшего агента всего " + caps.mem + "МБ RAM → OOM/зависание"); bump("block"); }
    else if (need > caps.mem * 0.6) { msgs.push("⚠ -j " + jobs + " нагрузит память агента (" + caps.mem + "МБ) — на грани"); bump("warn"); }
    if (caps.cpu && jobs > caps.cpu * 2) { msgs.push("⚠ -j " + jobs + " при " + caps.cpu + " CPU агента — перегруз процессора"); bump("warn"); }
  } else if (jobs > 4) { msgs.push("⚠ -j " + jobs + " высоковат — на слабой ноде риск OOM"); bump("warn"); }
  SCAN_BLOCKED = (level === "block");
  const opt = caps.mem ? Math.max(1, Math.floor(caps.mem * 0.6 / PER)) : 2;
  if (!msgs.length) {
    el.innerHTML = '<div class="adv adv-ok">✓ параметры в норме' + (caps.mem ? " · агент " + caps.mem + "МБ, оптимум -j ≤ " + opt : "") + "</div>";
  } else {
    el.innerHTML = '<div class="adv adv-' + (level === "block" ? "block" : "warn") + '">' +
      (level === "block" ? "<b>🚫 ОПАСНО для железа — запуск будет заблокирован:</b><br>" : "<b>⚠ Внимание:</b><br>") +
      msgs.map((m) => "· " + m).join("<br>") +
      (caps.mem ? '<br><span class="muted">рекомендую -j ≤ ' + opt + " для агента " + caps.mem + "МБ</span>" : "") + "</div>";
  }
}
["sc-ports-mode", "sc-ports-val", "sc-timing", "sc-jobs", "sc-nopre", "sc-pn"].forEach((id) => {
  const e = $("#" + id); if (e) { e.addEventListener("input", scanAdvice); e.addEventListener("change", scanAdvice); }
});

$("#sc-start").addEventListener("click", async () => {
  scanAdvice();
  if (SCAN_BLOCKED) {
    $("#scan-result").innerHTML = '<div class="adv adv-block">🚫 Стоп, бро. Эти параметры положат агента (OOM). Скан НЕ запущен — снизь параллелизм/порты (см. предупреждение выше).</div>';
    return;
  }
  const opts = { ports: { mode: $("#sc-ports-mode").value, value: $("#sc-ports-val").value },
    timing: +$("#sc-timing").value, jobs: +$("#sc-jobs").value,
    no_udp: $("#sc-noudp").checked, no_tcp: $("#sc-notcp").checked,
    skip_disc: $("#sc-pn").checked, no_preflight: $("#sc-nopre").checked };
  try {
    const r = await api("/jobs", { method: "POST", body: { opts, mode: $("#sc-mode").value, diff_against: $("#sc-diff").value || null } });
    $("#scan-result").textContent = "джоба запущена: " + r.job_id + "\nсмотри «Обзор» и «Отчёты» — чанки разъезжаются по агентам";
  } catch (e) { $("#scan-result").textContent = "ошибка: " + e.message; }
});
TAB_LOADERS.scan = async () => { scanAdvice(); try { const jobs = await api("/jobs"); $("#sc-diff").innerHTML = '<option value="">— нет —</option>' + jobs.map((j) => `<option value="${j.id}">${j.id}</option>`).join(""); } catch (e) {} };

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
window.viewReport = async (id) => {
  $("#report-title").textContent = id;
  $("#report-actions").innerHTML =
    `<button class="mini" onclick="window.open('/api/jobs/${id}/report','_blank')">HTML ↗</button>
     <button class="mini" onclick="window.open('/api/jobs/${id}/artifact/report.md','_blank')">.md</button>
     <button class="mini" onclick="window.open('/api/jobs/${id}/artifact/findings.csv','_blank')">.csv</button>
     <button class="mini" onclick="window.open('/api/jobs/${id}/artifact/findings.json','_blank')">.json</button>`;
  const view = $("#report-view");
  view.innerHTML = '<div class="empty">загрузка…</div>';
  try {
    const f = await api(`/jobs/${id}/artifact/findings.json`);
    view.innerHTML = renderReport(Array.isArray(f) ? f : []);
  } catch (e) { view.innerHTML = '<div class="empty">отчёт ещё не готов</div>'; }
};
const SEV_ORD = { Critical: 0, High: 1, Medium: 2, Low: 3, Info: 4 };
function renderReport(f) {
  if (!f.length) return '<div class="empty">находок нет — чисто</div>';
  const byHost = {}, sevCount = {};
  f.forEach((x) => { (byHost[x.host] = byHost[x.host] || []).push(x); sevCount[x.severity] = (sevCount[x.severity] || 0) + 1; });
  const chips = Object.keys(sevCount).sort((a, b) => (SEV_ORD[a] ?? 9) - (SEV_ORD[b] ?? 9))
    .map((s) => `<span class="badge ${esc(s)}">${esc(s)} ${sevCount[s]}</span>`).join(" ");
  const hosts = Object.keys(byHost).sort();
  const summary = `<div class="rep-summary"><div><b>${hosts.length}</b> хостов · <b>${f.length}</b> CVE</div><div class="rep-chips">${chips}</div></div>`;
  const sections = hosts.map((h) => {
    const rows = byHost[h].slice().sort((a, b) => (SEV_ORD[a.severity] ?? 9) - (SEV_ORD[b.severity] ?? 9) || (b.cvss || 0) - (a.cvss || 0));
    return `<details class="rep-host" open><summary><b>${esc(h)}</b> <span class="muted">${rows.length} CVE</span></summary>
      <table class="rep-table"><tr><th>CVE</th><th>CVSS</th><th>Severity</th></tr>
      ${rows.map((r) => `<tr><td><code>${esc(r.cve)}</code></td><td>${r.cvss ?? ""}</td><td><span class="badge ${esc(r.severity)}">${esc(r.severity)}</span></td></tr>`).join("")}
      </table></details>`;
  }).join("");
  return summary + sections;
}
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
    const r = await api("/loot");
    const box = $("#captures-list");
    if (!r.items || !r.items.length) { box.innerHTML = '<div class="empty">лута пока нет — закрепись на цели выше</div>'; return; }
    box.innerHTML = `<div class="muted" style="margin-bottom:.7rem">закреплений <b>${r.captures}</b> · хостов <b>${r.hosts}</b> · флагов <b>${r.flags}</b></div>`
      + r.items.map(lootCard).join("");
  } catch (e) {}
}
function lootCard(i) {
  const loot = i.loot || {};
  const blocks = Object.keys(loot).map((k) =>
    `<div class="loot-k">${esc(k)}</div><pre class="loot-pre">${esc(String(loot[k]).trim())}</pre>`).join("");
  const log = (i.log || []).length
    ? `<details class="loot-log"><summary>лог операции</summary><pre class="loot-pre">${esc(i.log.join("\n"))}</pre></details>` : "";
  const flag = i.flag ? `<div class="loot-flag">🏳 ${esc(i.flag)}</div>` : "";
  return `<div class="loot-card">
    <div class="loot-h"><span>🚩 <b>${esc(i.target)}</b>:${i.port} <span class="loot-cve">${esc(i.cve)}</span></span>
      <span class="muted">${new Date(i.ts * 1000).toLocaleString("ru-RU")}</span></div>
    ${flag}${blocks || '<div class="muted">модуль не вернул содержимого лута</div>'}
    ${i.marker ? `<div class="muted" style="margin-top:.5rem">маркер: <code>${esc(i.marker)}</code></div>` : ""}
    <div class="btns" style="margin-top:.6rem">
      <button class="mini" onclick="pivotScan('${esc(i.target)}','${esc(i.cve)}')">🛰 развед-скан сети за узлом (pivot)</button>
      <button class="mini danger" onclick="pivotExploit('${esc(i.target)}','${esc(i.cve)}')">🎯 захватить скрытые через pivot</button>
    </div>
    ${log}</div>`;
}
const WEB_CVE = ["CVE-2021-41773", "CVE-2014-6271", "CVE-2017-5638",
                 "CVE-2018-7600", "CVE-2012-1823", "CVE-2017-12617"];
const PIVOT_CVE_BY_PORT = {   // какие эксплойты пробовать по открытому порту скрытой цели
  80: WEB_CVE, 8080: WEB_CVE,
  21: ["CVE-2011-2523"],
};
window.pivotExploit = async (pivotHost, pivotCve) => {
  try {
    const exps = await api("/exploiters");
    if (!exps.length) { alert("нет ноды с ролью exploiter — назначь во вкладке «Агенты»"); return; }
    const all = await api("/pivot/hosts");
    const targets = all.filter((h) => h.pivot === pivotHost && (h.ports || []).some((p) => PIVOT_CVE_BY_PORT[p]));
    if (!targets.length) { alert("за этим узлом нет скрытых целей с известным эксплойтом. Сначала «развед-скан (pivot)»."); return; }
    if (!confirm(`Эксплуатировать ${targets.length} скрытых целей через плацдарм ${pivotHost}?\nАтака пойдёт ЧЕРЕЗ захваченный узел.`)) return;
    let msg = "";
    for (const h of targets) {
      const port = (h.ports || []).find((p) => PIVOT_CVE_BY_PORT[p]);
      let done = false;
      for (const cve of PIVOT_CVE_BY_PORT[port]) {
        const r = await api("/pivot/exploit", { method: "POST", body: {
          agent_id: exps[0].id, pivot_host: pivotHost, pivot_cve: pivotCve,
          hidden_target: h.hidden_ip, hidden_cve: cve, port } });
        if (r.ok && r.success) { msg += `  ${h.hidden_ip}: ✓ ${r.flag || "захвачено"} (${cve})\n`; done = true; break; }
      }
      if (!done) msg += `  ${h.hidden_ip}: ✗ не взято\n`;
    }
    alert(`Эксплуатация скрытых целей ЧЕРЕЗ pivot ${pivotHost}:\n${msg}\nВзятые стали фиолетовыми в графе.`);
    loadCaptures();
  } catch (e) { alert("ошибка: " + e.message); }
};
window.pivotScan = async (host, cve) => {
  try {
    const exps = await api("/exploiters");
    if (!exps.length) { alert("нет ноды с ролью exploiter — назначь во вкладке «Агенты»"); return; }
    const subnet = prompt("Скрытая подсеть за узлом (префикс /24):", "10.66.0");
    if (!subnet) return;
    const r = await api("/pivot/scan", { method: "POST", body: { agent_id: exps[0].id, host, cve, subnet } });
    if (r.ok) {
      const list = (r.hosts || []).map((h) => `  ${h.ip}  [${(h.ports || []).join(", ")}]`).join("\n");
      alert(`Pivot через ${host} — трафик пошёл в скрытую сеть.\nНайдено ${r.hosts.length} хостов:\n${list || "  (пусто)"}\n\nОни появились в графе за реле-узлом.`);
    } else alert("pivot не удался: " + (r.error || "см. лог"));
    loadCaptures();
  } catch (e) { alert("ошибка: " + e.message); }
};

// ── VPN ─────────────────────────────────────────────────────────────────────
TAB_LOADERS.vpn = loadVpn;
async function loadVpn() {
  try {
    const v = await api("/vpn");
    const live = (v.peers || []).filter((p) => p.handshake && p.handshake !== "нет").length;
    $("#vpn-status").innerHTML = `<div class="stat-list">
      <div class="kv"><span>Интерфейс</span><b style="color:${v.up ? "var(--good)" : "var(--bad)"}">${esc(v.iface || "awg0")} ${v.up ? "↑ поднят" : "↓ выкл"}</b></div>
      <div class="kv"><span>Порт (вход туннеля)</span><b>${v.listen_port || 51820}/udp</b></div>
      <div class="kv"><span>Адрес хоста в туннеле</span><b>${esc(v.server_ip || "10.9.0.1")}</b></div>
      <div class="kv"><span>Пиров всего</span><b>${v.peer_count || 0}</b></div>
      <div class="kv"><span>С активным handshake</span><b style="color:${live ? "var(--good)" : "var(--muted)"}">${live}</b></div>
    </div>`;
  } catch (e) { $("#vpn-status").innerHTML = '<div class="empty">VPN недоступен</div>'; }
  loadAdminVpn();
}
async function loadAdminVpn() {
  try {
    const list = await api("/vpn/admin");
    $("#avpn-list").innerHTML = list.length ? list.map((a) =>
      `<div class="row"><span class="dot ok"></span><div class="grow"><b>${esc(a.name)}</b>
        <span class="muted">${esc(a.tunnel_ip || "")}</span></div>
      <button class="mini success" onclick="window.open('/api/vpn/admin/${encodeURIComponent(a.name)}/conf','_blank')">скачать .conf</button>
      <button class="mini danger" onclick="delAdminVpn('${esc(a.name)}')">удалить</button></div>`).join("")
      : '<div class="empty">конфигов нет — создай первый</div>';
  } catch (e) {}
}
$("#avpn-create").addEventListener("click", async () => {
  const name = $("#avpn-name").value.trim();
  try {
    const r = await api("/vpn/admin", { method: "POST", body: { name } });
    $("#avpn-name").value = "";
    window.open(`/api/vpn/admin/${encodeURIComponent(r.name)}/conf`, "_blank");   // сразу скачать
    loadAdminVpn();
  } catch (e) { alert("ошибка: " + e.message); }
});
window.delAdminVpn = async (name) => {
  if (!confirm(`Удалить админ-доступ ${name}? (пир снимется, конфиг перестанет работать)`)) return;
  try { await api(`/vpn/admin/${encodeURIComponent(name)}`, { method: "DELETE" }); } catch (e) { alert(e.message); }
  loadAdminVpn();
};

// ── БЭКАПЫ ──────────────────────────────────────────────────────────────────
TAB_LOADERS.backups = loadBackups;
async function loadBackups() {
  try {
    const list = await api("/backups");
    $("#backups-list").innerHTML = list.length ? list.map((b) => {
      const kb = (b.size / 1024).toFixed(0);
      const dt = b.mtime ? new Date(b.mtime * 1000).toLocaleString() : "";
      const ver = b.version ? " · v" + esc(b.version) : "";
      const nm = encodeURIComponent(b.name);
      return `<div class="row"><span data-ic="save"></span>
      <div class="grow"><b>${esc(b.reason || "manual")}</b> <span class="muted">${dt}${ver} · ${kb} KB</span><br>
      <span class="muted" style="font-size:.8em">${esc(b.name)}</span></div>
      <a class="mini" href="/api/backups/${nm}/download">скачать</a>
      <button class="mini" onclick="restoreBackup('${b.name}')">восстановить</button>
      <button class="mini" onclick="deleteBackup('${b.name}')">удалить</button></div>`;
    }).join("") : '<div class="empty">бэкапов нет</div>';
    GWFX && GWFX.icons($("#backups-list"));
  } catch (e) {}
}
$("#bk-create").addEventListener("click", async () => { $("#bk-msg").textContent = "создаю снимок…"; try { const r = await api("/backups", { method: "POST" }); $("#bk-msg").textContent = "создан: " + r.name; } catch (e) { $("#bk-msg").textContent = "ошибка: " + e.message; } loadBackups(); });
$("#bk-upload").addEventListener("change", async (ev) => {
  const f = ev.target.files[0]; if (!f) return;
  $("#bk-msg").textContent = "загружаю " + f.name + "…";
  const fd = new FormData(); fd.append("file", f);
  try { const r = await fetch("/api/backups/upload", { method: "POST", credentials: "same-origin", body: fd }); const d = await r.json(); if (!r.ok) throw new Error(d.detail || r.statusText); $("#bk-msg").textContent = "загружен: " + d.name; }
  catch (e) { $("#bk-msg").textContent = "ошибка загрузки: " + e.message; }
  ev.target.value = ""; loadBackups();
});
window.restoreBackup = async (n) => { if (confirm("Восстановить из бэкапа?\nТекущее состояние сохранится в pre-restore копию.")) { try { const r = await api(`/backups/${encodeURIComponent(n)}/restore`, { method: "POST" }); alert("Восстановлено строк: " + r.restored_rows + (r.vpn_reloaded ? " · VPN перечитан" : " · VPN НЕ перечитан") + (r.safety ? "\nстраховка: " + r.safety : "")); } catch (e) { alert("ошибка: " + e.message); } loadBackups(); } };
window.deleteBackup = async (n) => { if (confirm("Удалить бэкап " + n + "?")) { try { await api(`/backups/${encodeURIComponent(n)}`, { method: "DELETE" }); } catch (e) { alert("ошибка: " + e.message); } loadBackups(); } };

// ── ОБНОВЛЕНИЯ ──────────────────────────────────────────────────────────────
TAB_LOADERS.updates = async () => {
  try {
    const agents = await api("/agents");
    $("#up-agents").innerHTML = agents.length ? agents.map((a) => `<div class="row"><div class="grow"><b>${esc(a.name)}</b> <span class="muted">${esc(a.tunnel_ip || "")}</span></div>
      <button class="mini" onclick="updAgentApi('${a.id}')">обновить (API)</button></div>`).join("") : '<div class="empty">нод нет</div>';
  } catch (e) {}
};
$("#up-host-git").addEventListener("click", async () => {
  const logEl = $("#up-host-log");
  const t0 = Math.floor(Date.now() / 1000);
  logEl.textContent = "ставлю заявку на обновление хоста…";
  try { const r = await api("/update/host", { method: "POST", body: { method: "git" } });
    logEl.textContent = (r.log || []).join("\n") + "\n" + (r.note || ""); }
  catch (e) { logEl.textContent = "ошибка: " + e.message; return; }
  logEl.textContent += "\n\nжду выполнения демоном (git + пересборка, до ~2-3 мин)…";
  for (let i = 0; i < 60; i++) {                 // ~4 мин; backend отвалится на пересборке
    await new Promise((r) => setTimeout(r, 4000));
    let s = null;
    try { s = await api("/update/status"); } catch (e) { logEl.textContent += "\n· backend пересобирается…"; continue; }
    if (!s || !s.ts || s.ts < t0) continue;      // старый/пустой статус — ждём наш
    if (s.status === "running") { logEl.textContent += "\n· обновление идёт…"; continue; }
    if (s.status === "updated") { logEl.textContent += `\n✓ ОБНОВЛЕНО: ${s.from} → ${s.to}`; loadVersion(); return; }
    if (s.status === "uptodate") { logEl.textContent += `\n✓ уже актуально (${s.to}) — пересборка не требовалась`; loadVersion(); return; }
    if (s.status === "failed") { logEl.textContent += `\n✗ ошибка обновления — выполнен откат на ${s.to}`; return; }
  }
  logEl.textContent += "\n\n⚠ демон не ответил за ~4 мин. Запущен ли watch-демон на хосте?\n   sudo systemctl status pintest-update\n   (или разово из консоли: sudo bash host/update.sh)";
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

// ── КОНСОЛЬ ─────────────────────────────────────────────────────────────────
const CON = { sessions: {}, active: null, seq: 0, _names: {} };
TAB_LOADERS.console = loadConsoleNodes;
async function loadConsoleNodes() {
  try {
    const ags = await api("/agents");
    ags.forEach((a) => { CON._names[a.id] = a.name; });
    const online = ags.filter((a) => a.status === "online");
    let html = online.length
      ? `<optgroup label="Агенты (полный bash)">${online.map((a) => `<option value="agent:${a.id}">🖥 ${esc(a.name)} · ${esc(a.tunnel_ip || "")}</option>`).join("")}</optgroup>`
      : "";
    try {
      const [tgts, exps] = await Promise.all([api("/console/targets"), api("/exploiters")]);
      if (tgts.length && exps.length) {
        const ex = exps[0].id;
        html += `<optgroup label="Захваченные цели (командная консоль через foothold)">${
          tgts.map((t) => `<option value="target:${t.target}:${t.cve}:${ex}">🎯 ${esc(t.target)} · ${esc(t.cve)}</option>`).join("")}</optgroup>`;
      }
    } catch (e) {}
    $("#con-node").innerHTML = html || '<option value="">нет доступных узлов</option>';
  } catch (e) {}
}
$("#con-new").addEventListener("click", openConsole);
async function openConsole() {
  const val = $("#con-node").value;
  if (!val) { alert("нет узла — подключи агента или захвати цель"); return; }
  if (val.startsWith("agent:")) return openAgentConsole(val.slice(6));
  if (val.startsWith("target:")) { const p = val.split(":"); return openTargetConsole(p[1], p[2], p[3]); }
}
function mkTerm(mount) {
  const term = new Terminal({ fontSize: 13, fontFamily: "ui-monospace,Consolas,monospace",
    theme: { background: "#000000", foreground: "#c9d1d9" }, cursorBlink: true, scrollback: 5000, convertEol: true });
  const fit = new FitAddon.FitAddon(); term.loadAddon(fit); term.open(mount);
  return { term, fit };
}
async function openAgentConsole(aid) {
  let sid;
  try { const r = await api(`/console/${aid}`, { method: "POST", body: { cols: 120, rows: 30 } }); sid = r.sid; }
  catch (e) { alert("не удалось открыть консоль: " + e.message); return; }
  if (!sid) { alert("агент не открыл сессию"); return; }
  const key = aid + ":" + sid, n = ++CON.seq;
  const label = (CON._names[aid] || aid.slice(0, 6)) + " #" + n;
  const holder = $("#con-holder"); const emp = $("#con-empty"); if (emp) emp.style.display = "none";
  const mount = document.createElement("div"); mount.className = "con-term"; holder.appendChild(mount);
  const { term, fit } = mkTerm(mount);
  const sess = { aid, sid, key, term, fit, mount, offset: 0, alive: true, label, kind: "agent" };
  CON.sessions[key] = sess;
  term.onData((data) => { api(`/console/${aid}/${sid}/input`, { method: "POST", body: { data } }).catch(() => {}); });
  addConTab(sess); activateConsole(key);
  setTimeout(() => { try { fit.fit(); sendResize(sess); } catch (e) {} }, 40);
  pollConsole(sess);
}
function openTargetConsole(target, cve, agentId) {
  const n = ++CON.seq, key = "t:" + target + ":" + n;
  const holder = $("#con-holder"); const emp = $("#con-empty"); if (emp) emp.style.display = "none";
  const mount = document.createElement("div"); mount.className = "con-term"; holder.appendChild(mount);
  const { term, fit } = mkTerm(mount);
  const sess = { key, term, fit, mount, target, cve, agentId, cmd: "", alive: true, kind: "target",
    label: "🎯 " + target + " #" + n };
  CON.sessions[key] = sess;
  const prompt = () => term.write(`\r\n\x1b[36m${target}\x1b[0m$ `);
  term.writeln(`\x1b[2m# командная консоль захваченной цели ${target} (${cve}) — через её foothold\x1b[0m`);
  term.writeln("\x1b[2m# не PTY: каждая команда — отдельный вызов (cd/окружение не сохраняются)\x1b[0m");
  prompt();
  term.onData(async (d) => {
    if (!sess.alive) return;
    for (const ch of d) {
      if (ch === "\r") {
        const cmd = sess.cmd.trim(); sess.cmd = "";
        if (!cmd) { prompt(); continue; }
        term.write("\r\n");
        try {
          const r = await api("/console/target/exec", { method: "POST",
            body: { agent_id: sess.agentId, target: sess.target, cve: sess.cve, cmd } });
          if (r.ok) term.write((r.output || "").replace(/\n/g, "\r\n"));
          else term.write("\x1b[31m" + (r.error || "ошибка") + "\x1b[0m");
        } catch (e) { term.write("\x1b[31m" + e.message + "\x1b[0m"); }
        prompt();
      } else if (ch === "\x7f") {
        if (sess.cmd.length) { sess.cmd = sess.cmd.slice(0, -1); term.write("\b \b"); }
      } else if (ch >= " ") { sess.cmd += ch; term.write(ch); }
    }
  });
  addConTab(sess); activateConsole(key);
  setTimeout(() => { try { fit.fit(); term.focus(); } catch (e) {} }, 40);
}
function addConTab(sess) {
  const tab = document.createElement("div"); tab.className = "con-tab"; tab.dataset.key = sess.key;
  tab.innerHTML = `<span>${esc(sess.label)}</span><span class="x">✕</span>`;
  tab.addEventListener("click", (e) => {
    if (e.target.classList.contains("x")) closeConsole(sess.key); else activateConsole(sess.key);
  });
  $("#con-tabs").appendChild(tab); sess.tab = tab;
}
function activateConsole(key) {
  CON.active = key;
  Object.values(CON.sessions).forEach((s) => {
    const on = s.key === key;
    s.mount.classList.toggle("active", on);
    if (s.tab) s.tab.classList.toggle("active", on);
    if (on) setTimeout(() => { try { s.fit.fit(); sendResize(s); s.term.focus(); } catch (e) {} }, 20);
  });
}
async function closeConsole(key) {
  const s = CON.sessions[key]; if (!s) return;
  s.alive = false;
  if (s.kind === "agent") { try { await api(`/console/${s.aid}/${s.sid}`, { method: "DELETE" }); } catch (e) {} }
  try { s.term.dispose(); } catch (e) {}
  s.mount.remove(); if (s.tab) s.tab.remove(); delete CON.sessions[key];
  const rest = Object.keys(CON.sessions);
  if (rest.length) activateConsole(rest[rest.length - 1]);
  else { CON.active = null; const emp = $("#con-empty"); if (emp) emp.style.display = ""; }
}
function sendResize(s) {
  if (!s.alive || s.kind !== "agent") return;
  api(`/console/${s.aid}/${s.sid}/resize`, { method: "POST", body: { cols: s.term.cols, rows: s.term.rows } }).catch(() => {});
}
async function pollConsole(s) {
  while (s.alive) {
    try {
      const r = await api(`/console/${s.aid}/${s.sid}/output?since=${s.offset}`);
      if (r.gone) { s.term.write("\r\n\x1b[31m[сессия закрыта]\x1b[0m\r\n"); break; }
      if (r.data) { s.term.write(r.data); s.offset = r.offset; }
      if (!r.alive) { s.term.write("\r\n\x1b[33m[shell завершился]\x1b[0m\r\n"); break; }
    } catch (e) { await new Promise((res) => setTimeout(res, 1000)); continue; }
    await new Promise((res) => setTimeout(res, 250));
  }
  s.alive = false;
}
window.addEventListener("resize", () => { const s = CON.sessions[CON.active]; if (s) { try { s.fit.fit(); sendResize(s); } catch (e) {} } });

// ── старт ───────────────────────────────────────────────────────────────────
initFx(); initLoginLogo();
(async () => { try { const m = await api("/me"); m.authenticated ? showApp() : showLogin(); } catch (e) { showLogin(); } })();

// ─────────────── контекстное меню узла топологии (клик по узлу) ───────────────
let _topoMenu = null;
function closeTopoMenu() { if (_topoMenu) { _topoMenu.remove(); _topoMenu = null; } }
document.addEventListener("click", (e) => { if (!e.target.closest(".topo-menu")) closeTopoMenu(); });

async function _pickExploiter() {
  const exps = await api("/exploiters").catch(() => []);
  if (!exps.length) { alert("Нет ноды с ролью exploiter — назначь во вкладке «Агенты»."); return null; }
  return exps[0].id;
}
function _gotoTab(tab) { const a = document.querySelector(`.topbar nav a[data-tab="${tab}"]`); if (a) a.click(); }

window.onTopoNode = (node, ev) => {
  ev.stopPropagation();
  closeTopoMenu();
  const m = document.createElement("div");
  m.className = "topo-menu";
  m.style.left = Math.min(ev.clientX + 4, window.innerWidth - 220) + "px";
  m.style.top = Math.min(ev.clientY + 4, window.innerHeight - 180) + "px";
  const title = node.kind === "agent"
    ? `Агент ${node.name || node.id}` + (node.status ? ` · ${node.status}` : "")
    : `${node.ip}` + (node.hidden ? " · скрыт" : node.status ? ` · ${node.status}` : "");
  const rows = [`<div class="tm-title">${esc(title)}</div>`];
  const act = [];
  if (node.kind === "target") {
    if (node.status !== "captured") act.push(["🎯 Авто-эксплойт", () => autoExploitIp(node.ip)]);
    if (node.status === "captured") act.push(["🖥 Открыть консоль цели", () => _gotoTab("console")]);
    if (node.is_relay || node.status === "captured")
      act.push(["🛰 Pivot-авто (захватить за узлом)", () => pivotAutoNode(node.ip, (node.candidates || [])[0])]);
    act.push(["ℹ Показать в «Эксплуатации»", () => _gotoTab("exploit")]);
  } else if (node.kind === "agent") {
    act.push([`роли: ${(node.roles || []).join(", ") || "—"}`, null]);
    act.push([`туннель: ${node.tunnel_ip || "—"}`, null]);
    act.push(["🖥 Консоль агента", () => _gotoTab("console")]);
  }
  m.innerHTML = rows.join("");
  act.forEach(([label, fn]) => {
    const b = document.createElement("button");
    b.className = "tm-item" + (fn ? "" : " tm-info"); b.textContent = label;
    if (fn) b.onclick = () => { fn(); }; else b.disabled = true;
    m.appendChild(b);
  });
  document.body.appendChild(m);
  _topoMenu = m;
};

window.autoExploitIp = async (ip) => {
  closeTopoMenu();
  const aid = await _pickExploiter(); if (!aid) return;
  if (!confirm(`Авто-эксплуатировать ${ip}?\nДвижок сам подберёт CVE по находкам скана и попробует захватить.`)) return;
  try {
    const r = await api("/exploit/auto_ip", { method: "POST", body: { agent_id: aid, ip, confirm: true } });
    alert(`${ip}:\n кандидатов ${r.candidates || 0} · уязвимо ${r.exploitable || 0} · захвачено ${r.captured || 0}`);
  } catch (e) { alert("Ошибка авто-эксплуатации: " + e); }
  loadOverview();
};

window.pivotAutoNode = async (ip, cve) => {
  closeTopoMenu();
  if (!cve) { alert("У этого узла нет известного эксплойта для pivot (нужен захваченный/двудомный узел)."); return; }
  const aid = await _pickExploiter(); if (!aid) return;
  if (!confirm(`Pivot-авто через ${ip}:\nскан скрытой сети (10.66.0.0/24) + авто-захват найденного его трафиком?`)) return;
  try {
    const r = await api("/pivot/auto", { method: "POST", body: { agent_id: aid, pivot_host: ip, pivot_cve: cve, subnet: "10.66.0", confirm: true } });
    const cap = (r.captured || []).length;
    alert(`Pivot ${ip}: скрытых хостов ${(r.hosts || []).length} · захвачено ${cap}`);
  } catch (e) { alert("Ошибка pivot-авто: " + e); }
  loadOverview();
};
